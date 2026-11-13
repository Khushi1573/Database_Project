import csv
import math

import numpy as np
import pytest

from fidelity.adaptation import detect_adaptation
from fidelity.aggregate import aggregate, median_ci, summarize
from fidelity.plots import plot_all


# ---------------- adaptation detector ----------------

def test_detects_ramp_then_plateau():
    t = list(range(12))
    x = [0.0, 0.1, 0.3, 0.5, 0.7, 0.79, 0.80, 0.81, 0.80, 0.79, 0.80, 0.81]
    a = detect_adaptation(t, x, window=4, epsilon=0.03)
    assert a.adapted and a.index == 5 and a.time == 5
    assert a.plateau == pytest.approx(0.80, abs=0.01)


def test_not_adapted_when_still_moving():
    t = list(range(8))
    a = detect_adaptation(t, [0, .1, .2, .3, .4, .5, .6, .7], window=3, epsilon=0.05)
    assert not a.adapted and a.time is None


def test_already_stable_from_start():
    a = detect_adaptation(range(6), [0.5] * 6, window=3, epsilon=0.01)
    assert a.adapted and a.index == 0


def test_too_short_series():
    assert not detect_adaptation([0, 1], [1, 1], window=5).adapted


def test_bad_input():
    with pytest.raises(ValueError):
        detect_adaptation([0, 1, 2], [1, 2], window=1)
    with pytest.raises(ValueError):
        detect_adaptation([2, 1, 0], [1, 1, 1], window=1)


# ---------------- bootstrap aggregation ----------------

def test_median_ci_brackets_median_and_is_reproducible():
    vals = [12.1, 13.4, 11.8, 19.7, 12.9]
    m, lo, hi = median_ci(vals, seed=1)
    assert m == pytest.approx(12.9)
    assert lo <= m <= hi
    assert median_ci(vals, seed=1) == (m, lo, hi)


def test_median_ci_degenerate_and_empty():
    assert median_ci([0.0] * 5) == (0.0, 0.0, 0.0)
    assert all(math.isnan(v) for v in median_ci([]))


def test_summarize_skips_missing_and_traces_sources():
    rows = [
        {"cell": "B", "run": "1", "latency_us": "10", "Uh": "0.6", "source_file": "raw/B_1.json"},
        {"cell": "B", "run": "2", "latency_us": "12", "Uh": "",    "source_file": "raw/B_2.json"},
        {"cell": "B", "run": "3", "latency_us": "11", "Uh": "0.7", "source_file": "raw/B_3.json"},
    ]
    out = {s.metric: s for s in summarize(rows, ["latency_us", "Uh"])}
    assert out["latency_us"].n == 3 and out["latency_us"].median == 11
    assert out["Uh"].n == 2 and out["Uh"].runs == "1;3"
    assert out["Uh"].sources == "raw/B_1.json;raw/B_3.json"


def test_aggregate_end_to_end(tmp_path):
    rng = np.random.default_rng(0)
    runs = tmp_path / "runs.csv"
    with open(runs, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["cell", "run", "latency_us", "Uh", "Fs", "source_file"])
        w.writeheader()
        for cell, base, uh, fs in [("A", 20, 0, 1), ("B", 14, .6, 1), ("C", 20, 0, 0),
                                   ("D", 15, .55, 0), ("E", 8, 1, 1), ("F", 11, 0, 1)]:
            for r in range(1, 6):
                w.writerow({"cell": cell, "run": r,
                            "latency_us": round(base * rng.lognormal(0, .15), 2),
                            "Uh": uh, "Fs": fs, "source_file": f"raw/{cell}_{r}.json"})
    out = tmp_path / "summary.csv"
    s = aggregate(runs, out, ["latency_us", "Uh", "Fs"])
    assert len(s) == 18 and out.exists()
    lat = [x for x in s if x.metric == "latency_us"]
    assert all(x.ci_low <= x.median <= x.ci_high and x.n == 5 for x in lat)
    pngs = plot_all(s, tmp_path / "fig", {"latency_us": "Median point-lookup latency (µs)"})
    assert len(pngs) == 3 and all(p.stat().st_size > 0 for p in pngs)
