"""Bootstrap aggregation and summary CSV -- FR-15, FR-16, NFR-4.

Input: a per-run CSV with one row per (cell, run) and one column per metric,
plus a `source_file` column naming the raw telemetry file of that run.

Output: one row per (cell, metric) with the median, a bootstrap 95% CI
(percentile method, 10,000 resamples, fixed seed), n, and the list of runs
and source files the numbers came from, so every reported value can be traced
back to raw telemetry (NFR-4).

Why medians and the percentile bootstrap: latency distributions are skewed and
five repeats per cell are too few to assume normality. With n = 5 the
intervals are wide; that is reported, not hidden. The percentile method is
used instead of BCa because BCa's jackknife acceleration is unstable at n = 5.
"""

from __future__ import annotations

import csv
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from scipy.stats import bootstrap

N_RESAMPLES = 10_000
CONFIDENCE = 0.95
DEFAULT_SEED = 8157


@dataclass(frozen=True)
class Summary:
    cell: str
    metric: str
    n: int
    median: float
    ci_low: float
    ci_high: float
    runs: str
    sources: str

    def to_row(self) -> dict:
        return self.__dict__.copy()


def median_ci(values: Sequence[float], n_resamples: int = N_RESAMPLES,
              confidence: float = CONFIDENCE, seed: int = DEFAULT_SEED
              ) -> tuple[float, float, float]:
    x = np.asarray([v for v in values if v is not None and not np.isnan(v)], dtype=float)
    if x.size == 0:
        return (float("nan"),) * 3
    med = float(np.median(x))
    if x.size == 1 or np.all(x == x[0]):
        # degenerate sample: every resample has the same median
        return med, med, med
    rng = np.random.default_rng(seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = bootstrap((x,), np.median, n_resamples=n_resamples,
                        confidence_level=confidence, method="percentile",
                        random_state=rng, vectorized=True, axis=-1)
    return med, float(res.confidence_interval.low), float(res.confidence_interval.high)


def _to_float(v):
    if v is None or str(v).strip() == "" or str(v).lower() in {"na", "nan", "none"}:
        return float("nan")
    return float(v)


def summarize(rows: Iterable[dict], metrics: Sequence[str],
              seed: int = DEFAULT_SEED) -> list[Summary]:
    by_cell: dict[str, list[dict]] = {}
    for r in rows:
        by_cell.setdefault(str(r["cell"]), []).append(r)

    out: list[Summary] = []
    for cell in sorted(by_cell):
        runs = by_cell[cell]
        for m in metrics:
            vals = [_to_float(r.get(m)) for r in runs]
            kept = [(r, v) for r, v in zip(runs, vals) if not np.isnan(v)]
            med, lo, hi = median_ci([v for _, v in kept], seed=seed)
            out.append(Summary(
                cell=cell, metric=m, n=len(kept), median=med, ci_low=lo, ci_high=hi,
                runs=";".join(str(r.get("run", "")) for r, _ in kept),
                sources=";".join(str(r.get("source_file", "")) for r, _ in kept),
            ))
    return out


def read_runs(path: str | Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_summary(summaries: Sequence[Summary], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(Summary.__dataclass_fields__)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in summaries:
            w.writerow(s.to_row())
    return path


def aggregate(runs_csv: str | Path, out_csv: str | Path,
              metrics: Sequence[str], seed: int = DEFAULT_SEED) -> list[Summary]:
    """Pipeline entry point (the `aggregate` stage in D.4.2 section 6.4)."""
    summaries = summarize(read_runs(runs_csv), metrics, seed=seed)
    write_summary(summaries, out_csv)
    return summaries
