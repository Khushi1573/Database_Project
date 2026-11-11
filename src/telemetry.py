"""Background telemetry sampling and raw-result persistence.

Implements FR-13, FR-16, NFR-1, NFR-4, AC-8 (D.4.2 sections 4.4, 5.5, 5.6).
"""
from __future__ import annotations

import csv
import dataclasses
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from fidelity import ahi as ahi_mod

TELEMETRY_COLUMNS = [
    "run_id",
    "phase_no",
    "t_rel_s",
    "qps_read",
    "tps_write",
    "lat_avg_ms",
    "lat_p95_ms",
    "rows_modified",
    "ahi_hash_searches",
    "ahi_btree_searches",
]

GAP_THRESHOLD_SECONDS = 2.0


def enable_ahi_monitoring(connection) -> None:
    """Enable InnoDB AHI counters in information_schema.INNODB_METRICS."""
    cur = connection.cursor()
    try:
        cur.execute("SET GLOBAL innodb_monitor_enable = 'module_adaptive_hash'")
    finally:
        cur.close()


@dataclasses.dataclass
class RawSample:
    t_rel_s: float
    phase_no: int
    qps_read: float
    tps_write: float
    lat_avg_ms: float
    lat_p95_ms: float
    rows_modified: int
    ahi_hash_searches: int
    ahi_btree_searches: int


def _global_status(connection) -> dict[str, str]:
    cur = connection.cursor()
    try:
        cur.execute("SHOW GLOBAL STATUS")
        return {name: value for name, value in cur.fetchall()}
    finally:
        cur.close()


def _read_innodb_ahi_counters(connection) -> tuple[int, int]:
    """Read adaptive_hash_searches and adaptive_hash_searches_btree from
    information_schema.INNODB_METRICS (via fidelity.ahi)."""
    cur = connection.cursor()
    try:
        snap = ahi_mod.read_counters(cur)
        return snap.hash_searches, snap.btree_searches
    except Exception:  # noqa: BLE001 - safe fallback if metrics disabled on non-AHI cell
        return 0, 0
    finally:
        cur.close()


def _statement_latency(connection) -> tuple[float, float]:
    """Approximate avg/p95 statement latency in ms from performance_schema."""
    cur = connection.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT COUNT_STAR, SUM_TIMER_WAIT FROM performance_schema."
            "events_statements_summary_global_by_event_name "
            "WHERE EVENT_NAME IN ('statement/sql/select', 'statement/sql/update')"
        )
        rows = cur.fetchall()
        count_star = sum(r["COUNT_STAR"] or 0 for r in rows)
        sum_timer = sum(r["SUM_TIMER_WAIT"] or 0 for r in rows)
        avg_ms = (sum_timer / count_star / 1e9) if count_star else 0.0

        cur.execute(
            "SELECT BUCKET_NUMBER, BUCKET_TIMER_LOW, COUNT_BUCKET_AND_LOWER "
            "FROM performance_schema.events_statements_histogram_global "
            "ORDER BY BUCKET_NUMBER"
        )
        hist_rows = cur.fetchall()
        p95_ms = avg_ms
        if hist_rows:
            total = hist_rows[-1]["COUNT_BUCKET_AND_LOWER"] or 0
            if total > 0:
                target = 0.95 * total
                for row in hist_rows:
                    if (row["COUNT_BUCKET_AND_LOWER"] or 0) >= target:
                        p95_ms = (row["BUCKET_TIMER_LOW"] or 0) / 1e9
                        break
        return avg_ms, p95_ms
    finally:
        cur.close()


def sample_once(
    connection,
    prev_status: Optional[dict[str, str]],
    engine: str = "InnoDB",
) -> tuple[RawSample, dict[str, str]]:
    """Take one lightweight telemetry sample and return (sample, new_status)."""
    status = _global_status(connection)

    def delta(key: str) -> float:
        cur_val = float(status.get(key, 0) or 0)
        prev_val = float(prev_status.get(key, cur_val)) if prev_status else cur_val
        return max(cur_val - prev_val, 0.0)

    qps_read = delta("Com_select")
    tps_write = delta("Com_update")
    if engine.upper() == "INNODB":
        rows_modified = int(delta("Innodb_rows_updated"))
        ahi_hash_searches, ahi_btree_searches = _read_innodb_ahi_counters(connection)
    else:
        rows_modified = int(delta("Handler_update"))
        ahi_hash_searches, ahi_btree_searches = 0, 0

    lat_avg_ms, lat_p95_ms = _statement_latency(connection)

    sample = RawSample(
        t_rel_s=0.0,
        phase_no=0,
        qps_read=qps_read,
        tps_write=tps_write,
        lat_avg_ms=lat_avg_ms,
        lat_p95_ms=lat_p95_ms,
        rows_modified=rows_modified,
        ahi_hash_searches=ahi_hash_searches,
        ahi_btree_searches=ahi_btree_searches,
    )
    return sample, status


def sample_loop(
    connection_factory,
    stop_event: threading.Event,
    phase_no_ref: list[int],
    buffer: list[RawSample],
    interval_seconds: float = 1.0,
    engine: str = "InnoDB",
) -> None:
    """Background 1s sampler buffering RawSample rows in memory."""
    connection = connection_factory()
    prev_status: Optional[dict[str, str]] = None
    start = time.monotonic()
    try:
        while not stop_event.is_set():
            loop_start = time.monotonic()
            sample, prev_status = sample_once(connection, prev_status, engine=engine)
            sample.t_rel_s = round(time.monotonic() - start, 3)
            sample.phase_no = phase_no_ref[0]
            buffer.append(sample)

            elapsed = time.monotonic() - loop_start
            time.sleep(max(0.0, interval_seconds - elapsed))
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


def validate_continuity(samples: list[RawSample], active_phases: set[int]) -> list[str]:
    """Flag any gap > GAP_THRESHOLD_SECONDS during active workload phases (AC-8)."""
    warnings: list[str] = []
    active = [s for s in samples if s.phase_no in active_phases]
    active.sort(key=lambda s: s.t_rel_s)
    for prev, curr in zip(active, active[1:]):
        gap = curr.t_rel_s - prev.t_rel_s
        if gap > GAP_THRESHOLD_SECONDS:
            warnings.append(
                f"telemetry gap of {gap:.2f}s between t={prev.t_rel_s:.2f}s and "
                f"t={curr.t_rel_s:.2f}s during phase {curr.phase_no}"
            )
    return warnings


def flush_telemetry(run_id: str, samples: list[RawSample], results_dir: Path) -> Path:
    """Write buffered samples to results/raw/<run_id>_telemetry.csv."""
    out_path = results_dir / "raw" / f"{run_id}_telemetry.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(TELEMETRY_COLUMNS)
        for s in samples:
            writer.writerow([
                run_id,
                s.phase_no,
                s.t_rel_s,
                s.qps_read,
                s.tps_write,
                s.lat_avg_ms,
                s.lat_p95_ms,
                s.rows_modified,
                s.ahi_hash_searches,
                s.ahi_btree_searches,
            ])
    return out_path


def _git_commit_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parent.parent,
        )
        return result.stdout.strip() or "unknown"
    except FileNotFoundError:
        return "unknown"


def write_run_meta(
    run_id: str,
    results_dir: Path,
    *,
    cell: str,
    repeat: int,
    derived_seed: int,
    rows: int,
    dataset_fallback: bool,
    mysql_version: str,
    image_tag: str,
    cpu_sets: dict[str, str],
    phase_timestamps: list[dict],
    audit_record: dict,
    fs: float,
    uh: Optional[float],
    adaptation_times: list[dict],
    status: str,
    retries: int,
    continuity_warnings: Optional[list[str]] = None,
) -> Path:
    """Write results/raw/<run_id>_meta.json (D.4.2 section 4.4)."""
    meta = {
        "run_id": run_id,
        "cell": cell,
        "repeat": repeat,
        "derived_seed": derived_seed,
        "rows": rows,
        "dataset_fallback": dataset_fallback,
        "mysql_version": mysql_version,
        "image_tag": image_tag,
        "cpu_sets": cpu_sets,
        "phase_timestamps": phase_timestamps,
        "audit_record": audit_record,
        "Fs": fs,
        "Uh": uh,
        "adaptation_times": adaptation_times,
        "continuity_warnings": continuity_warnings or [],
        "status": status,
        "retries": retries,
        "git_commit_hash": _git_commit_hash(),
    }
    out_path = results_dir / "raw" / f"{run_id}_meta.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return out_path
