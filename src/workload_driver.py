"""Schema rendering, seeded dataset loading, and sysbench-driven workload
execution across the three read/write phases.

Implements FR-8, FR-9, FR-11, AC-5, AC-7 (D.4.2 section 5.4).
"""
from __future__ import annotations

import dataclasses
import hashlib
import random
import subprocess
import time
import zlib
from pathlib import Path
from typing import Optional

SCHEMA_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
LUA_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "lua" / "mixed_rw.lua"

# Rough per-row byte estimate for bench.sbtest1: id(4) + k(4) + payload(120)
# + per-row and index overhead (~52B). Used for the MEMORY fallback rule (FR-11).
BYTES_PER_ROW_ESTIMATE = 180


def render_schema(engine: str, index: str) -> str:
    """Render sql/schema.sql for the given engine/index combination.

    Per D.4.2 section 4.1, InnoDB uses a plain `PRIMARY KEY (id)` while
    MEMORY applies the cell's `USING {BTREE|HASH}` clause to PRIMARY KEY too.
    """
    template = SCHEMA_TEMPLATE_PATH.read_text(encoding="utf-8")
    primary_using = f" USING {index}" if engine.strip().upper() == "MEMORY" else ""
    return (
        template.replace("{{ENGINE}}", engine)
        .replace("{{PRIMARY_USING}}", primary_using)
        .replace("{{INDEX}}", index)
    )


def create_schema(connection, engine: str, index: str) -> None:
    """Create bench.sbtest1 BEFORE bulk loading (D.4.2 section 4.1)."""
    sql = render_schema(engine, index)
    cur = connection.cursor()
    for statement in filter(None, (s.strip() for s in sql.split(";"))):
        cur.execute(statement)
    connection.commit()


def estimate_bytes(rows: int) -> int:
    return rows * BYTES_PER_ROW_ESTIMATE


def choose_dataset(
    cell_engine: str,
    requested_rows: int,
    container_mem_limit_bytes: int,
) -> tuple[int, bool]:
    """Return (actual_rows, dataset_fallback).

    FR-11 / AC-7: If cell.engine == 'MEMORY' and estimate_bytes(rows) > 0.70 *
    container_mem_limit, fall back to 1_000_000 rows and record fallback=True.
    """
    if cell_engine.upper() == "MEMORY" and estimate_bytes(requested_rows) > 0.70 * container_mem_limit_bytes:
        return 1_000_000, True
    return requested_rows, False


def derive_seed(study_seed: int, cell_id: str, repeat: int) -> int:
    """Deterministically derive a per-(cell, repeat) seed from the study seed."""
    payload = f"{study_seed}:{cell_id}:{repeat}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


@dataclasses.dataclass
class LoadResult:
    row_count: int
    checksum: str


def load_dataset(connection, rows: int, seed: int, batch_size: int = 1000) -> LoadResult:
    """Deterministically generate and bulk-load `rows` rows into bench.sbtest1."""
    rng = random.Random(seed)
    cur = connection.cursor()

    checksum = 0
    inserted = 0
    row_id = 1
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    while row_id <= rows:
        batch_rows = []
        batch_end = min(row_id + batch_size, rows + 1)
        for rid in range(row_id, batch_end):
            k = rng.randint(1, rows)
            payload = "".join(rng.choices(alphabet, k=120))
            batch_rows.append((rid, k, payload))
            checksum = zlib.crc32(f"{rid}|{k}|{payload}".encode("utf-8"), checksum)

        placeholders = ",".join(["(%s,%s,%s)"] * len(batch_rows))
        flat_values = [v for row in batch_rows for v in row]
        cur.execute(
            f"INSERT INTO bench.sbtest1 (id, k, payload) VALUES {placeholders}",
            flat_values,
        )
        inserted += len(batch_rows)
        row_id = batch_end

    connection.commit()
    return LoadResult(row_count=inserted, checksum=f"{checksum:08x}")


def verify_load(connection, expected: LoadResult) -> bool:
    """Verify loaded row count and streamed CRC32 checksum before audit."""
    cur = connection.cursor()
    cur.execute("SELECT COUNT(*) FROM bench.sbtest1")
    (actual_count,) = cur.fetchone()
    if actual_count != expected.row_count:
        return False

    cur.execute("SELECT id, k, payload FROM bench.sbtest1 ORDER BY id")
    checksum = 0
    while True:
        chunk = cur.fetchmany(5000)
        if not chunk:
            break
        for rid, k, payload in chunk:
            checksum = zlib.crc32(f"{rid}|{k}|{payload}".encode("utf-8"), checksum)
    return f"{checksum:08x}" == expected.checksum


@dataclasses.dataclass
class IndexUsageCheck:
    explain_type: str
    explain_key: Optional[str]
    io_waits_count: int
    confirmed: bool


def confirm_index_usage(
    bench_connection,
    audit_connection=None,
    sample_k: int = 1,
) -> IndexUsageCheck:
    """Confirm the lookup query uses `idx_k` via EXPLAIN and cross-check
    `performance_schema.table_io_waits_summary_by_index_usage`."""
    b_cur = bench_connection.cursor(dictionary=True)
    b_cur.execute("EXPLAIN SELECT payload FROM bench.sbtest1 WHERE k = %s", (int(sample_k),))
    row = b_cur.fetchone() or {}
    explain_type = str(row.get("type", ""))
    explain_key = row.get("key")

    p_conn = audit_connection or bench_connection
    p_cur = p_conn.cursor(dictionary=True)
    p_cur.execute(
        "SELECT COUNT_READ FROM performance_schema.table_io_waits_summary_by_index_usage "
        "WHERE OBJECT_SCHEMA = 'bench' AND OBJECT_NAME = 'sbtest1' AND INDEX_NAME = 'idx_k'"
    )
    io_row = p_cur.fetchone()
    io_waits_count = int(io_row["COUNT_READ"]) if io_row else 0

    confirmed = (explain_key == "idx_k")
    return IndexUsageCheck(
        explain_type=explain_type,
        explain_key=explain_key,
        io_waits_count=io_waits_count,
        confirmed=confirmed,
    )


@dataclasses.dataclass
class PhaseResult:
    phase_no: int
    read_pct: int
    start_ts: float
    end_ts: float
    sysbench_stdout: str
    sysbench_stderr: str
    return_code: int


def run_sysbench_phase(
    host: str,
    port: int,
    user: str,
    password: str,
    table_size: int,
    read_pct: int,
    threads: int,
    duration_seconds: int,
    phase_no: int,
    seed: Optional[int] = None,
) -> PhaseResult:
    """Invoke sysbench with lua/mixed_rw.lua for one phase and record timestamps."""
    cmd = [
        "sysbench",
        str(LUA_SCRIPT_PATH),
        f"--mysql-host={host}",
        f"--mysql-port={port}",
        f"--mysql-user={user}",
        f"--mysql-password={password}",
        "--mysql-db=bench",
        f"--table_size={table_size}",
        f"--read_pct={read_pct}",
        f"--threads={threads}",
        f"--time={duration_seconds}",
        "--report-interval=1",
    ]
    if seed is not None:
        # sysbench --rand-seed expects a 32-bit unsigned int
        cmd.append(f"--rand-seed={int(seed) & 0xFFFFFFFF}")
    cmd.append("run")

    start_ts = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    end_ts = time.time()
    return PhaseResult(
        phase_no=phase_no,
        read_pct=read_pct,
        start_ts=start_ts,
        end_ts=end_ts,
        sysbench_stdout=proc.stdout,
        sysbench_stderr=proc.stderr,
        return_code=proc.returncode,
    )


def run_warmup(
    host: str,
    port: int,
    user: str,
    password: str,
    table_size: int,
    threads: int,
    warmup_seconds: int,
    seed: Optional[int] = None,
) -> PhaseResult:
    """Run an unrecorded warm-up phase (study.warmup_seconds) at 95% reads."""
    return run_sysbench_phase(
        host=host,
        port=port,
        user=user,
        password=password,
        table_size=table_size,
        read_pct=95,
        threads=threads,
        duration_seconds=warmup_seconds,
        phase_no=0,
        seed=seed,
    )


def run_phases(
    host: str,
    port: int,
    user: str,
    password: str,
    table_size: int,
    threads: int,
    phases: list[tuple[int, int]],
    seed: Optional[int] = None,
) -> list[PhaseResult]:
    """Execute the 3 measured phases [(read_pct, seconds), ...] in order."""
    results: list[PhaseResult] = []
    for i, (read_pct, duration) in enumerate(phases, start=1):
        phase_seed = (seed + i) if seed is not None else None
        results.append(
            run_sysbench_phase(
                host=host,
                port=port,
                user=user,
                password=password,
                table_size=table_size,
                read_pct=read_pct,
                threads=threads,
                duration_seconds=duration,
                phase_no=i,
                seed=phase_seed,
            )
        )
    return results
