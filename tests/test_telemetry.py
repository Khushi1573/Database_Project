"""Telemetry continuity gap detection and CSV flush (FR-13, AC-8)."""
from __future__ import annotations

import csv

from src.telemetry import RawSample, TELEMETRY_COLUMNS, flush_telemetry, validate_continuity


def _sample(t, phase):
    return RawSample(
        t_rel_s=t,
        phase_no=phase,
        qps_read=1.0,
        tps_write=1.0,
        lat_avg_ms=1.0,
        lat_p95_ms=2.0,
        rows_modified=1,
        ahi_hash_searches=0,
        ahi_btree_searches=0,
    )


def test_no_gap_when_samples_are_continuous():
    samples = [_sample(t, 1) for t in [0, 1, 2, 3, 4]]
    assert validate_continuity(samples, active_phases={1}) == []


def test_flags_gap_over_threshold():
    samples = [_sample(0, 1), _sample(1, 1), _sample(4.5, 1), _sample(5.5, 1)]
    warnings = validate_continuity(samples, active_phases={1})
    assert len(warnings) == 1
    assert "gap" in warnings[0]


def test_ignores_gaps_outside_active_phases():
    samples = [_sample(0, 0), _sample(10, 0), _sample(10.5, 1), _sample(11.5, 1)]
    assert validate_continuity(samples, active_phases={1, 2, 3}) == []


def test_flush_telemetry_writes_expected_columns(tmp_path):
    samples = [_sample(0, 1), _sample(1, 1)]
    path = flush_telemetry("run_test", samples, tmp_path)
    assert path.exists()
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
    assert header == TELEMETRY_COLUMNS
