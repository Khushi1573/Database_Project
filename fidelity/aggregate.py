"""Bootstrap aggregation and summary CSV -- FR-15, FR-16, NFR-3, NFR-4.

Provides both:
- Per-(cell, metric) aggregation (`Summary`, `summarize`, `aggregate`)
- Phase-aware per-(cell_id, metric, phase) aggregation (`MetricRecord`,
  `PhaseSummary`, `summarize_by_phase`, `write_phase_summary_csv`) matching
  D.4.2 section 4.4.
"""

from __future__ import annotations

import csv
import dataclasses
import warnings
from collections import defaultdict
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


@dataclasses.dataclass(frozen=True)
class MetricRecord:
    """One observation: a single repeat's value for a (cell, metric, phase)."""

    cell_id: str
    metric: str
    phase: int
    value: float
    run_id: str = ""
    source_file: str = ""


@dataclasses.dataclass(frozen=True)
class PhaseSummary:
    cell_id: str
    metric: str
    phase: int
    median: float
    ci95_low: float
    ci95_high: float
    n_repeats: int
    runs: str = ""
    sources: str = ""


def median_ci(values: Sequence[float], n_resamples: int = N_RESAMPLES,
              confidence: float = CONFIDENCE, seed: int = DEFAULT_SEED
              ) -> tuple[float, float, float]:
    x = np.asarray([v for v in values if v is not None and not np.isnan(v)], dtype=float)
    if x.size == 0:
        return (float("nan"),) * 3
    med = float(np.median(x))
    if x.size == 1 or np.all(x == x[0]):
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


def summarize_by_phase(
    records: Iterable[MetricRecord],
    n_resamples: int = N_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> list[PhaseSummary]:
    """Group records by (cell_id, metric, phase) and compute median + 95%
    percentile bootstrap CI for each group."""
    groups: dict[tuple[str, str, int], list[MetricRecord]] = defaultdict(list)
    for rec in records:
        if rec.value is not None and not np.isnan(rec.value):
            groups[(rec.cell_id, rec.metric, rec.phase)].append(rec)

    out: list[PhaseSummary] = []
    for (cell_id, metric, phase), grp in sorted(groups.items()):
        med, lo, hi = median_ci([g.value for g in grp], n_resamples=n_resamples, seed=seed)
        out.append(
            PhaseSummary(
                cell_id=cell_id,
                metric=metric,
                phase=phase,
                median=med,
                ci95_low=lo,
                ci95_high=hi,
                n_repeats=len(grp),
                runs=";".join(g.run_id for g in grp if g.run_id),
                sources=";".join(g.source_file for g in grp if g.source_file),
            )
        )
    return out


def read_runs(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_summary(summaries: Sequence[Summary], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(Summary.__dataclass_fields__)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in summaries:
            w.writerow(s.to_row())
    return path


def write_phase_summary_csv(summaries: Sequence[PhaseSummary], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "cell_id", "metric", "phase", "median", "ci95_low", "ci95_high",
            "n_repeats", "runs", "sources",
        ])
        for s in summaries:
            writer.writerow([
                s.cell_id, s.metric, s.phase, s.median, s.ci95_low, s.ci95_high,
                s.n_repeats, s.runs, s.sources,
            ])
    return path


def aggregate(runs_csv: str | Path, out_csv: str | Path,
              metrics: Sequence[str], seed: int = DEFAULT_SEED) -> list[Summary]:
    """Pipeline entry point (the `aggregate` stage in D.4.2 section 6.4)."""
    summaries = summarize(read_runs(runs_csv), metrics, seed=seed)
    write_summary(summaries, out_csv)
    return summaries
