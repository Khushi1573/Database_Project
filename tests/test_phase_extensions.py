"""Tests for D.4.2 extensions: phase adaptation, phase-aware aggregation, behavioral crossval."""
from __future__ import annotations

from fidelity.adaptation import detect_phase_adaptation, moving_median
from fidelity.aggregate import MetricRecord, summarize_by_phase
from fidelity.crossval import cross_validate_behavioral


def test_moving_median_smooths_spikes():
    values = [1, 1, 1, 100, 1, 1, 1]
    smoothed = moving_median(values, window=3)
    assert smoothed[3] == 1.0


def test_detect_phase_adaptation_stabilizes():
    t = list(range(25))
    values = [0.0] * 5 + [0.80] * 20
    result = detect_phase_adaptation(
        t, values, transition_t=5, cell_id="B", transition_no=1,
        median_window=1, stabilization_window=10, epsilon=0.02,
    )
    assert result.stabilized is True
    assert result.adaptation_time_s == 0.0


def test_detect_phase_adaptation_censored():
    t = list(range(20))
    values = [0.1] * 10 + [0.1 if i % 2 == 0 else 0.9 for i in range(10)]
    result = detect_phase_adaptation(
        t, values, transition_t=10, cell_id="D", transition_no=1,
        median_window=1, stabilization_window=5, epsilon=0.02,
    )
    assert result.stabilized is False
    assert result.adaptation_time_s is None
    assert "censored" in result.detail


def test_summarize_by_phase_groups_correctly():
    records = [
        MetricRecord(cell_id="A", metric="lat_p95_ms", phase=1, value=1.0, run_id="A_r1"),
        MetricRecord(cell_id="A", metric="lat_p95_ms", phase=1, value=3.0, run_id="A_r2"),
        MetricRecord(cell_id="A", metric="lat_p95_ms", phase=2, value=9.0, run_id="A_r1"),
    ]
    keyed = {(s.cell_id, s.metric, s.phase): s for s in summarize_by_phase(records, n_resamples=200)}
    assert keyed[("A", "lat_p95_ms", 1)].n_repeats == 2
    assert keyed[("A", "lat_p95_ms", 1)].median == 2.0
    assert keyed[("A", "lat_p95_ms", 2)].n_repeats == 1


def test_behavioral_crossval():
    r_same = cross_validate_behavioral([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], metric="lat_p95_ms", phase=1)
    assert r_same.consistent is True
    r_diff = cross_validate_behavioral([1.0, 1.1, 0.9, 1.05, 0.95], [50.0, 51.0, 49.0, 50.5, 49.5], metric="lat_p95_ms", phase=1)
    assert r_diff.consistent is False
    assert r_diff.p_value < 0.05
