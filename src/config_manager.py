"""Configuration loading/validation and MySQL container lifecycle management.

Implements FR-1, FR-2, FR-3, NFR-5, NFR-7, NFR-9 (D.4.2 sections 5.2, 6.2, 7.2).

Exit-code contract (see src/cli.py):
    0 - success
    2 - invalid configuration
    3 - provisioning / container failure (e.g. healthcheck timeout)
    4 - run/report failure
"""
from __future__ import annotations

import dataclasses
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Optional

import yaml

try:
    import docker  # type: ignore
    from docker.errors import APIError, NotFound  # type: ignore
except ImportError:  # pragma: no cover - optional for offline validation
    docker = None  # type: ignore
    APIError = NotFound = Exception  # type: ignore


VALID_ENGINES = {"InnoDB", "MEMORY"}
VALID_INDEXES = {"BTREE", "HASH"}
VALID_ROWS = {1_000_000, 10_000_000}


class ConfigValidationError(ValueError):
    """Raised when a study/cell configuration fails validation (exit code 2)."""


class ProvisionError(RuntimeError):
    """Raised when provisioning a container fails (exit code 3)."""


class ProvisionTimeoutError(ProvisionError):
    """Raised when a container does not become healthy within the timeout."""


@dataclasses.dataclass
class PhaseConfig:
    read_pct: int
    seconds: int


@dataclasses.dataclass
class CellConfig:
    cell_id: str
    engine: str
    index: str
    ahi: str  # "on" | "off" | "n/a"

    def validate(self) -> list[str]:
        """Return a list of human-readable validation errors (empty if valid)."""
        errors: list[str] = []

        if self.engine not in VALID_ENGINES:
            errors.append(
                f"Cell {self.cell_id}: Unsupported engine {self.engine!r} "
                f"(must be one of {sorted(VALID_ENGINES)})"
            )
        if self.index not in VALID_INDEXES:
            errors.append(
                f"Cell {self.cell_id}: Unsupported index {self.index!r} "
                f"(must be one of {sorted(VALID_INDEXES)})"
            )

        if self.engine == "MEMORY" and self.ahi != "n/a":
            errors.append(
                f"Cell {self.cell_id}: AHI setting not allowed on MEMORY cells "
                f"(must be 'n/a', got {self.ahi!r})"
            )
        if self.engine == "InnoDB" and self.ahi not in {"on", "off"}:
            errors.append(
                f"Cell {self.cell_id}: AHI must be 'on' or 'off' for InnoDB, "
                f"got {self.ahi!r}"
            )

        return errors


@dataclasses.dataclass
class StudyParams:
    name: str
    mysql_image: str
    seed: int
    rows: int
    repeats: int
    warmup_seconds: int
    phases: list[PhaseConfig]
    threads: int

    @property
    def phase_seconds(self) -> list[int]:
        return [p.seconds for p in self.phases]

    @property
    def read_pct_phases(self) -> list[int]:
        return [p.read_pct for p in self.phases]

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.mysql_image:
            errors.append("study.mysql_image must be specified (e.g. 'mysql:8.4')")
        if self.rows not in VALID_ROWS:
            errors.append(f"study.rows must be one of {sorted(VALID_ROWS)}, got {self.rows!r}")
        if self.repeats < 5:
            errors.append(f"study.repeats must be >= 5, got {self.repeats!r}")
        if self.warmup_seconds < 0:
            errors.append(f"study.warmup_seconds must be >= 0, got {self.warmup_seconds!r}")
        if len(self.phases) != 3:
            errors.append("study.phases must have exactly 3 entries (95/10/95 reads)")
        for idx, phase in enumerate(self.phases, start=1):
            if not (0 <= phase.read_pct <= 100):
                errors.append(f"study.phases[{idx}].read_pct must be in 0..100, got {phase.read_pct!r}")
            if phase.seconds < 1:
                errors.append(f"study.phases[{idx}].seconds must be >= 1, got {phase.seconds!r}")
        if self.threads < 1:
            errors.append(f"study.threads must be >= 1, got {self.threads!r}")
        return errors


@dataclasses.dataclass
class AdaptationParams:
    window: int = 10
    epsilon: float = 0.02
    median_window: int = 5

    def validate(self) -> list[str]:
        errors = []
        if self.window < 1:
            errors.append(f"adaptation.window must be >= 1, got {self.window!r}")
        if self.median_window < 1:
            errors.append(f"adaptation.median_window must be >= 1, got {self.median_window!r}")
        if not (0 < self.epsilon < 1):
            errors.append(f"adaptation.epsilon must be in (0,1), got {self.epsilon!r}")
        return errors


@dataclasses.dataclass
class MySQLParams:
    image: str = "mysql:8.4"
    innodb_buffer_pool_size: str = "2G"
    max_heap_table_size_memory: str = "4G"
    container_mem_limit: str = "4g"
    healthcheck_timeout_seconds: int = 60


@dataclasses.dataclass
class StudyConfig:
    study: StudyParams
    cpu_pinning: dict[str, str]
    adaptation: AdaptationParams
    cells: dict[str, CellConfig]
    mysql: MySQLParams
    docker_network: str
    source_path: Path

    def validate(self) -> list[str]:
        """Aggregate all validation errors across study/cells/cpu pinning."""
        errors: list[str] = []
        errors.extend(self.study.validate())
        errors.extend(self.adaptation.validate())

        if "driver" not in self.cpu_pinning or "mysql" not in self.cpu_pinning:
            errors.append("cpu_pinning must define both 'driver' and 'mysql' cpuset ranges")
        else:
            for role, cpuset in self.cpu_pinning.items():
                if not re.fullmatch(r"\d+(-\d+)?(,\d+(-\d+)?)*", str(cpuset)):
                    errors.append(f"cpu_pinning.{role} is not a valid cpuset range: {cpuset!r}")

        if not self.cells:
            errors.append("study must define at least one cell")
        for cell in self.cells.values():
            errors.extend(cell.validate())

        return errors


def validate_cell(cell_or_name: CellConfig | str, cell_data: Optional[dict[str, Any]] = None) -> list[str]:
    """Validate a CellConfig or a (cell_name, dict) pair."""
    if isinstance(cell_or_name, CellConfig):
        return cell_or_name.validate()
    data = cell_data or {}
    return CellConfig(
        cell_id=str(cell_or_name),
        engine=str(data.get("engine", "")),
        index=str(data.get("index", "")),
        ahi=str(data.get("ahi", "")),
    ).validate()


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_phases(study_raw: dict[str, Any]) -> list[PhaseConfig]:
    if "phases" in study_raw:
        return [
            PhaseConfig(read_pct=int(p["read_pct"]), seconds=int(p["seconds"]))
            for p in (study_raw["phases"] or [])
        ]
    read_pcts = list(study_raw.get("read_pct_phases", [95, 10, 95]))
    seconds_list = list(study_raw.get("phase_seconds", [300, 300, 300]))
    return [PhaseConfig(read_pct=int(r), seconds=int(s)) for r, s in zip(read_pcts, seconds_list)]


def load_study(path: str | Path, overrides: Optional[dict[str, Any]] = None) -> StudyConfig:
    """Load and validate configs/study.yaml (supports D.4.2 section 6.2 layout)."""
    path = Path(path)
    if not path.exists():
        raise ConfigValidationError(f"config file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ConfigValidationError(f"invalid YAML in {path}: {exc}") from exc

    if overrides:
        raw = _deep_merge(raw, overrides)

    try:
        study_raw = raw["study"]
        mysql_raw = raw.get("mysql", {})
        mysql_image = str(study_raw.get("mysql_image") or mysql_raw.get("image") or "mysql:8.4")
        study_params = StudyParams(
            name=str(study_raw["name"]),
            mysql_image=mysql_image,
            seed=int(study_raw["seed"]),
            rows=int(study_raw["rows"]),
            repeats=int(study_raw["repeats"]),
            warmup_seconds=int(study_raw.get("warmup_seconds", 120)),
            phases=_parse_phases(study_raw),
            threads=int(study_raw.get("threads", 8)),
        )
    except KeyError as exc:
        raise ConfigValidationError(f"missing required study field: {exc}") from exc

    # Support cpu_pinning under study: (D.4.2 §6.2) or at root
    cpu_pinning = dict(study_raw.get("cpu_pinning") or raw.get("cpu_pinning") or {})

    adaptation_raw = raw.get("adaptation", {})
    adaptation_params = AdaptationParams(
        window=int(adaptation_raw.get("window", 10)),
        epsilon=float(adaptation_raw.get("epsilon", 0.02)),
        median_window=int(adaptation_raw.get("median_window", 5)),
    )

    cells: dict[str, CellConfig] = {}
    for cell_id, cell_raw in (raw.get("cells") or {}).items():
        try:
            cells[str(cell_id)] = CellConfig(
                cell_id=str(cell_id),
                engine=str(cell_raw["engine"]),
                index=str(cell_raw["index"]),
                ahi=str(cell_raw["ahi"]),
            )
        except KeyError as exc:
            raise ConfigValidationError(f"cell {cell_id} missing required field: {exc}") from exc

    mysql_params = MySQLParams(
        image=mysql_image,
        innodb_buffer_pool_size=str(mysql_raw.get("innodb_buffer_pool_size", "2G")),
        max_heap_table_size_memory=str(mysql_raw.get("max_heap_table_size_memory", "4G")),
        container_mem_limit=str(mysql_raw.get("container_mem_limit", "4g")),
        healthcheck_timeout_seconds=int(mysql_raw.get("healthcheck_timeout_seconds", 60)),
    )

    docker_network = str((raw.get("docker") or {}).get("network", "audit-net"))

    config = StudyConfig(
        study=study_params,
        cpu_pinning=cpu_pinning,
        adaptation=adaptation_params,
        cells=cells,
        mysql=mysql_params,
        docker_network=docker_network,
        source_path=path,
    )

    errors = config.validate()
    if errors:
        raise ConfigValidationError(
            "invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return config


@dataclasses.dataclass
class DBCredentials:
    root_password: str
    audit_user: str
    audit_password: str
    bench_user: str
    bench_password: str

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "DBCredentials":
        env = env if env is not None else os.environ
        missing = [
            k
            for k in (
                "MYSQL_ROOT_PASSWORD",
                "AUDIT_DB_USER",
                "AUDIT_DB_PASSWORD",
                "BENCH_DB_USER",
                "BENCH_DB_PASSWORD",
            )
            if not env.get(k)
        ]
        if missing:
            raise ConfigValidationError(
                "missing required credential environment variables "
                f"(set them in .env, never hardcode them): {missing}"
            )
        return cls(
            root_password=env["MYSQL_ROOT_PASSWORD"],
            audit_user=env["AUDIT_DB_USER"],
            audit_password=env["AUDIT_DB_PASSWORD"],
            bench_user=env["BENCH_DB_USER"],
            bench_password=env["BENCH_DB_PASSWORD"],
        )


@dataclasses.dataclass
class Instance:
    cell: CellConfig
    container_id: str
    container_name: str
    host: str
    port: int
    network: str


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _bootstrap_users(instance: Instance, creds: DBCredentials) -> None:
    """Create least-privilege `audit` and `bench` users (D.4.2 section 7.2)."""
    import mysql.connector

    conn = mysql.connector.connect(
        host=instance.host,
        port=instance.port,
        user="root",
        password=creds.root_password,
        connection_timeout=10,
    )
    try:
        cur = conn.cursor()
        cur.execute("CREATE DATABASE IF NOT EXISTS bench")
        cur.execute(
            "CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s",
            (creds.audit_user, creds.audit_password),
        )
        cur.execute("GRANT SELECT ON performance_schema.* TO %s@'%%'", (creds.audit_user,))
        cur.execute("GRANT SELECT ON information_schema.* TO %s@'%%'", (creds.audit_user,))
        cur.execute("GRANT SELECT ON bench.* TO %s@'%%'", (creds.audit_user,))
        cur.execute("GRANT PROCESS, REPLICATION CLIENT, SYSTEM_VARIABLES_ADMIN ON *.* TO %s@'%%'", (creds.audit_user,))

        cur.execute(
            "CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s",
            (creds.bench_user, creds.bench_password),
        )
        cur.execute("GRANT ALL PRIVILEGES ON bench.* TO %s@'%%'", (creds.bench_user,))
        cur.execute("FLUSH PRIVILEGES")
        conn.commit()
    finally:
        conn.close()


def provision(
    cell: CellConfig,
    study: StudyConfig,
    run_id: str,
    creds: Optional[DBCredentials] = None,
) -> Instance:
    """Start a pinned mysql:8.4 container for `cell` and wait up to 60s for readiness."""
    if docker is None:
        raise ProvisionError("docker SDK not installed; cannot provision containers")

    creds = creds or DBCredentials.from_env()
    client = docker.from_env()

    container_name = f"audit-{cell.cell_id}-{run_id}"
    port = _free_port()

    cnf_overrides = [
        "--performance-schema=ON",
        f"--innodb-buffer-pool-size={study.mysql.innodb_buffer_pool_size}",
    ]
    if cell.engine == "InnoDB":
        ahi_value = "ON" if cell.ahi == "on" else "OFF"
        cnf_overrides.append(f"--innodb-adaptive-hash-index={ahi_value}")
    if cell.engine == "MEMORY":
        cnf_overrides.append(f"--max-heap-table-size={study.mysql.max_heap_table_size_memory}")

    try:
        client.networks.get(study.docker_network)
    except NotFound:
        client.networks.create(study.docker_network, driver="bridge")

    container = client.containers.run(
        study.mysql.image,
        name=container_name,
        detach=True,
        remove=False,
        environment={"MYSQL_ROOT_PASSWORD": creds.root_password},
        ports={"3306/tcp": ("127.0.0.1", port)},
        cpuset_cpus=study.cpu_pinning.get("mysql"),
        mem_limit=study.mysql.container_mem_limit,
        network=study.docker_network,
        command=cnf_overrides,
        labels={"study": study.study.name, "cell": cell.cell_id, "run_id": run_id},
    )

    instance = Instance(
        cell=cell,
        container_id=container.id,
        container_name=container_name,
        host="127.0.0.1",
        port=port,
        network=study.docker_network,
    )

    deadline = time.monotonic() + study.mysql.healthcheck_timeout_seconds
    healthy = False
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((instance.host, instance.port), timeout=2):
                pass
            import mysql.connector

            conn = mysql.connector.connect(
                host=instance.host,
                port=instance.port,
                user="root",
                password=creds.root_password,
                connection_timeout=2,
            )
            conn.close()
            healthy = True
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1)

    if not healthy:
        try:
            destroy(instance)
        except Exception:  # noqa: BLE001
            pass
        raise ProvisionTimeoutError(
            f"Cell {cell.cell_id}: container {container_name} did not become "
            f"healthy within {study.mysql.healthcheck_timeout_seconds}s "
            f"(last error: {last_error})"
        )

    _bootstrap_users(instance, creds)
    return instance


def destroy(instance: Instance) -> None:
    """Stop and remove the container for `instance` (idempotent cleanup)."""
    if docker is None:
        return
    client = docker.from_env()
    try:
        container = client.containers.get(instance.container_id)
        container.stop(timeout=10)
        container.remove(force=True)
    except NotFound:
        pass
    except APIError:
        pass
