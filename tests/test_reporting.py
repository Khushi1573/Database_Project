"""Report generation from fixture raw results (FR-7, FR-15..FR-17, AC-4, AC-9, AC-10)."""
from __future__ import annotations

import csv
import json

import pytest

from src.reporting import generate_report, load_valid_runs


def _write_run(results_dir, run_id, cell, repeat=1, status="success", requested_type="BTREE", built_type="BTREE", fs=1.0):
    raw_dir = results_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "run_id": run_id,
        "cell": cell,
        "repeat": repeat,
        "derived_seed": repeat,
        "rows": 1000,
        "dataset_fallback": False,
        "mysql_version": "mysql:8.4",
        "image_tag": "mysql:8.4",
        "cpu_sets": {},
        "phase_timestamps": [],
        "audit_record": {
            "cell": cell,
            "engine": "INNODB",
            "index": "idx_k",
            "requested_type": requested_type,
            "built_type": built_type,
            "requested_columns": "k",
            "built_columns": "k",
            "requested_unique": False,
            "built_unique": False,
            "type_match": requested_type == built_type,
            "columns_match": True,
            "unique_match": True,
            "sources_agree": True,
            "Fs": int(fs),
        },
        "Fs": fs,
        "Uh": 0.0,
        "adaptation_times": [],
        "status": status,
        "retries": 0,
        "git_commit_hash": "deadbeef",
    }
    (raw_dir / f"{run_id}_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    telemetry_path = raw_dir / f"{run_id}_telemetry.csv"
    with open(telemetry_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "run_id", "phase_no", "t_rel_s", "qps_read", "tps_write",
            "lat_avg_ms", "lat_p95_ms", "rows_modified",
            "ahi_hash_searches", "ahi_btree_searches",
        ])
        for phase in (1, 2, 3):
            for t in range(3):
                writer.writerow([run_id, phase, t, 10.0, 2.0, 1.5, 3.0, 5, 0, 0])


def test_load_valid_runs_excludes_failed(tmp_path):
    _write_run(tmp_path, "A_r1", "A", repeat=1)
    _write_run(tmp_path, "A_r2", "A", repeat=2, status="failed")
    runs = load_valid_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].run_id == "A_r1"


def test_generate_report_produces_expected_outputs(tmp_path):
    _write_run(tmp_path, "A_r1", "A", repeat=1, requested_type="BTREE", built_type="BTREE", fs=1.0)
    _write_run(tmp_path, "A_r2", "A", repeat=2, requested_type="BTREE", built_type="BTREE", fs=1.0)
    _write_run(tmp_path, "C_r1", "C", repeat=1, requested_type="HASH", built_type="BTREE", fs=0.0)
    _write_run(tmp_path, "C_r2", "C", repeat=2, requested_type="HASH", built_type="BTREE", fs=0.0)

    report_path = generate_report(tmp_path)

    assert report_path.exists()
    assert (tmp_path / "aggregated" / "summary.csv").exists()
    assert (tmp_path / "figures" / "fs_uh_by_cell.png").exists()
    assert (tmp_path / "figures" / "lookup_latency_by_phase.png").exists()
    assert (tmp_path / "figures" / "write_throughput_by_phase.png").exists()
    assert (tmp_path / "figures" / "ahi_adaptation_time.png").exists()

    html = report_path.read_text(encoding="utf-8")
    assert "Measuring Index-Request Fidelity" in html
    assert "Substitution confirmed?" in html
    assert "match" in html


def test_generate_report_raises_on_no_valid_runs(tmp_path):
    with pytest.raises(RuntimeError):
        generate_report(tmp_path)
