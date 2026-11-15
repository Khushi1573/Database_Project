"""Experiment orchestrator: builds the cells x repeats schedule, runs each
cell/repeat with provisioning, retry and resumability, and always cleans
up containers.

Implements FR-10, AC-6 (D.4.2 section 5.1).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import random
import threading
from pathlib import Path
from typing import Callable, Optional

from fidelity import ahi as ahi_mod
from fidelity import auditor as auditor_mod
from fidelity.adaptation import detect_phase_adaptation
from fidelity.auditor import IndexSpec
from src import config_manager, telemetry, workload_driver
from src.config_manager import Instance, StudyConfig

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_INVALID_CONFIG = 2
EXIT_PROVISION_FAILURE = 3
EXIT_RUN_FAILURE = 4

MAX_AUTO_RETRIES = 1


@dataclasses.dataclass
class RunTask:
    cell_id: str
    repeat: int
    run_id: str
    seed: int


@dataclasses.dataclass
class RunOutcome:
    task: RunTask
    status: str  # "success" | "failed"
    retries: int
    error: Optional[str] = None


def build_schedule(study: StudyConfig) -> list[RunTask]:
    """Generate the cells x repeats schedule, shuffled deterministically with study.seed."""
    tasks = []
    for cell_id in study.cells:
        for repeat in range(1, study.study.repeats + 1):
            seed = workload_driver.derive_seed(study.study.seed, cell_id, repeat)
            run_id = f"{cell_id}_r{repeat}"
            tasks.append(RunTask(cell_id=cell_id, repeat=repeat, run_id=run_id, seed=seed))

    rng = random.Random(study.study.seed)
    rng.shuffle(tasks)
    return tasks


def _run_already_completed(run_id: str, results_dir: Path) -> bool:
    meta_path = results_dir / "raw" / f"{run_id}_meta.json"
    if not meta_path.exists():
        return False
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        return meta.get("status") == "success"
    except (json.JSONDecodeError, OSError):
        return False


def _parse_bytes(size_str: str) -> int:
    s = str(size_str).strip().upper()
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
    if s[-1] in multipliers:
        return int(float(s[:-1]) * multipliers[s[-1]])
    return int(s)


def _interval_hash_ratios(samples: list[telemetry.RawSample]) -> tuple[list[float], list[float]]:
    """Compute per-sample AHI hash-search ratio r = d_hash / (d_hash + d_btree)."""
    times: list[float] = []
    ratios: list[float] = []
    for prev, curr in zip(samples, samples[1:]):
        dh = max(0, curr.ahi_hash_searches - prev.ahi_hash_searches)
        db = max(0, curr.ahi_btree_searches - prev.ahi_btree_searches)
        total = dh + db
        times.append(curr.t_rel_s)
        ratios.append((dh / total) if total > 0 else 0.0)
    return times, ratios


def run_single(
    task: RunTask,
    study: StudyConfig,
    results_dir: Path,
    *,
    retries: int = 0,
    creds: Optional[config_manager.DBCredentials] = None,
) -> RunOutcome:
    """Provision a container, load data, audit Fs, run warm-up + 3 phases,
    collect telemetry/Uh/adaptation data, persist raw artifacts, and destroy container."""
    cell = study.cells[task.cell_id]
    instance: Optional[Instance] = None
    bench_conn = None
    audit_conn = None

    import mysql.connector

    def connect(instance_: Instance, user: str, password: str):
        return mysql.connector.connect(
            host=instance_.host,
            port=instance_.port,
            user=user,
            password=password,
            connection_timeout=10,
        )

    try:
        instance = config_manager.provision(cell, study, task.run_id, creds=creds)
        creds_ = creds or config_manager.DBCredentials.from_env()

        bench_conn = connect(instance, creds_.bench_user, creds_.bench_password)
        audit_conn = connect(instance, creds_.audit_user, creds_.audit_password)

        workload_driver.create_schema(bench_conn, cell.engine, cell.index)

        container_mem_limit = _parse_bytes(study.mysql.container_mem_limit)
        rows, fallback = workload_driver.choose_dataset(cell.engine, study.study.rows, container_mem_limit)

        load_result = workload_driver.load_dataset(bench_conn, rows, task.seed)
        if not workload_driver.verify_load(bench_conn, load_result):
            raise RuntimeError(f"{task.run_id}: dataset load verification failed")

        audit_cur = audit_conn.cursor()
        try:
            audit_result = auditor_mod.audit(
                audit_cur,
                task.cell_id,
                "bench",
                "sbtest1",
                IndexSpec("idx_k", cell.index, ("k",), False),
            )
            fs = float(audit_result.fs)

            if cell.engine == "InnoDB":
                telemetry.enable_ahi_monitoring(audit_conn)

            index_check = workload_driver.confirm_index_usage(bench_conn, audit_conn)

            # Warm-up before measured phases (D.4.2 section 5.4)
            warmup_res = workload_driver.run_warmup(
                instance.host,
                instance.port,
                creds_.bench_user,
                creds_.bench_password,
                rows,
                study.study.threads,
                study.study.warmup_seconds,
                seed=task.seed,
            )
            if warmup_res.return_code != 0:
                raise RuntimeError(f"{task.run_id}: warmup failed (rc={warmup_res.return_code}): {warmup_res.sysbench_stderr}")

            ahi_before = (
                ahi_mod.read_counters(audit_cur)
                if (cell.engine == "InnoDB" and cell.ahi == "on")
                else None
            )

            telemetry_buffer: list[telemetry.RawSample] = []
            stop_event = threading.Event()
            phase_no_ref = [1]
            sampler_thread = threading.Thread(
                target=telemetry.sample_loop,
                args=(
                    lambda: connect(instance, creds_.audit_user, creds_.audit_password),
                    stop_event,
                    phase_no_ref,
                    telemetry_buffer,
                ),
                kwargs={"engine": cell.engine},
                daemon=True,
            )
            sampler_thread.start()

            phase_timestamps = []
            try:
                for i, phase_cfg in enumerate(study.study.phases, start=1):
                    phase_no_ref[0] = i
                    result = workload_driver.run_sysbench_phase(
                        instance.host,
                        instance.port,
                        creds_.bench_user,
                        creds_.bench_password,
                        rows,
                        phase_cfg.read_pct,
                        study.study.threads,
                        phase_cfg.seconds,
                        i,
                        seed=task.seed + i,
                    )
                    if result.return_code != 0:
                        raise RuntimeError(
                            f"{task.run_id}: phase {i} sysbench failed (rc={result.return_code}): {result.sysbench_stderr}"
                        )
                    phase_timestamps.append({
                        "phase_no": i,
                        "read_pct": phase_cfg.read_pct,
                        "start_ts": result.start_ts,
                        "end_ts": result.end_ts,
                    })
            finally:
                stop_event.set()
                sampler_thread.join(timeout=5)

            ahi_after = (
                ahi_mod.read_counters(audit_cur)
                if (cell.engine == "InnoDB" and cell.ahi == "on")
                else None
            )
            uh = ahi_mod.compute_uh(
                cell.engine,
                ahi_enabled=(cell.ahi == "on"),
                built_type=audit_result.built.index_type,
                before=ahi_before,
                after=ahi_after,
            )
        finally:
            audit_cur.close()

        continuity_warnings = telemetry.validate_continuity(telemetry_buffer, active_phases={1, 2, 3})
        for w in continuity_warnings:
            logger.warning("%s: %s", task.run_id, w)

        adaptation_times: list[dict] = []
        if cell.ahi == "on":
            t_series, ratio_series = _interval_hash_ratios(telemetry_buffer)
            cumulative = 0.0
            for idx, pt in enumerate(phase_timestamps[:-1], start=1):
                cumulative += pt["end_ts"] - pt["start_ts"]
                next_end = cumulative + (
                    phase_timestamps[idx]["end_ts"] - phase_timestamps[idx]["start_ts"]
                )
                adaptation = detect_phase_adaptation(
                    t_series,
                    ratio_series,
                    transition_t=cumulative,
                    cell_id=task.cell_id,
                    transition_no=idx,
                    median_window=study.adaptation.median_window,
                    stabilization_window=study.adaptation.window,
                    epsilon=study.adaptation.epsilon,
                    end_t=next_end,
                )
                adaptation_times.append(dataclasses.asdict(adaptation))

        mysql_version = study.mysql.image
        audit_record = audit_result.to_row()
        audit_record["index_usage_confirmed"] = index_check.confirmed
        audit_record["explain_key"] = index_check.explain_key

        telemetry.flush_telemetry(task.run_id, telemetry_buffer, results_dir)
        telemetry.write_run_meta(
            task.run_id,
            results_dir,
            cell=task.cell_id,
            repeat=task.repeat,
            derived_seed=task.seed,
            rows=rows,
            dataset_fallback=fallback,
            mysql_version=mysql_version,
            image_tag=study.mysql.image,
            cpu_sets=study.cpu_pinning,
            phase_timestamps=phase_timestamps,
            audit_record=audit_record,
            fs=fs,
            uh=uh,
            adaptation_times=adaptation_times,
            status="success",
            retries=retries,
            continuity_warnings=continuity_warnings,
        )

        return RunOutcome(task=task, status="success", retries=retries)

    except Exception as exc:  # noqa: BLE001
        logger.error("%s failed: %s", task.run_id, exc)
        return RunOutcome(task=task, status="failed", retries=retries, error=str(exc))

    finally:
        for conn in (bench_conn, audit_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        if instance is not None:
            config_manager.destroy(instance)


def run_matrix(
    study: StudyConfig,
    results_dir: Path,
    *,
    resume: bool = True,
    run_fn: Callable[..., RunOutcome] = run_single,
) -> int:
    """Run the full cells x repeats experiment matrix. Returns exit code (0, 2, 3, or 4)."""
    errors = study.validate()
    if errors:
        logger.error("invalid configuration: %s", errors)
        return EXIT_INVALID_CONFIG

    schedule = build_schedule(study)
    any_failure = False

    for task in schedule:
        if resume and _run_already_completed(task.run_id, results_dir):
            logger.info("%s already completed, skipping (resumability)", task.run_id)
            continue

        outcome = run_fn(task, study, results_dir, retries=0)
        attempt = 0
        while outcome.status != "success" and attempt < MAX_AUTO_RETRIES:
            attempt += 1
            logger.warning("%s failed, retrying (attempt %d)", task.run_id, attempt)
            outcome = run_fn(task, study, results_dir, retries=attempt)

        if outcome.status != "success":
            logger.error("%s failed twice; excluding from aggregation", task.run_id)
            any_failure = True
            telemetry.write_run_meta(
                task.run_id,
                results_dir,
                cell=task.cell_id,
                repeat=task.repeat,
                derived_seed=task.seed,
                rows=study.study.rows,
                dataset_fallback=False,
                mysql_version=study.mysql.image,
                image_tag=study.mysql.image,
                cpu_sets=study.cpu_pinning,
                phase_timestamps=[],
                audit_record={},
                fs=0.0,
                uh=0.0,
                adaptation_times=[],
                status="failed",
                retries=attempt,
            )

    return EXIT_RUN_FAILURE if any_failure else EXIT_OK
