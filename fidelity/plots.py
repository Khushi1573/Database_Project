"""Summary figures -- FR-17 (layout from Screen S1).

One figure per metric: the median of each cell as a dot with its bootstrap
95% CI as a thin whisker. One series per figure, so no legend; the title names
the metric and the subtitle states n and the CI method, so wide intervals at
n = 5 are visible rather than hidden behind a bar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .aggregate import Summary  # noqa: E402

MARK = "#2a6fb0"
INK = "#1f2328"
MUTED = "#6b7280"
GRID = "#e5e7eb"


def plot_metric(summaries: Sequence[Summary], metric: str, out_path: str | Path,
                ylabel: str | None = None, title: str | None = None) -> Path:
    rows = [s for s in summaries if s.metric == metric and not np.isnan(s.median)]
    if not rows:
        raise ValueError(f"no data for metric {metric!r}")
    rows.sort(key=lambda s: s.cell)
    x = np.arange(len(rows))
    med = np.array([s.median for s in rows])
    lo = med - np.array([s.ci_low for s in rows])
    hi = np.array([s.ci_high for s in rows]) - med

    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=200)
    ax.errorbar(x, med, yerr=[lo, hi], fmt="o", color=MARK, ecolor=MARK,
                elinewidth=2, capsize=4, markersize=8,
                markeredgecolor="white", markeredgewidth=1.5)
    ax.set_xticks(x, [s.cell for s in rows], color=INK)
    ax.set_xlabel("Cell", color=MUTED)
    ax.set_ylabel(ylabel or metric, color=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED)
    n_vals = sorted({s.n for s in rows})
    ax.set_title(title or metric, loc="left", color=INK, fontsize=11, pad=16)
    ax.text(0, 1.02, f"Median per cell, bootstrap 95% CI (percentile, 10,000 "
            f"resamples), n = {', '.join(map(str, n_vals))} runs",
            transform=ax.transAxes, fontsize=7.5, color=MUTED)
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def plot_all(summaries: Sequence[Summary], out_dir: str | Path,
             labels: dict[str, str] | None = None) -> list[Path]:
    labels = labels or {}
    metrics = sorted({s.metric for s in summaries})
    return [plot_metric(summaries, m, Path(out_dir) / f"{m}.png",
                        ylabel=labels.get(m), title=labels.get(m, m))
            for m in metrics]
