"""Summary figures -- FR-17 (layout from Wireframe S1).

Provides both:
- `plot_metric` and `plot_all` over `Summary` objects (dot + CI whisker per cell)
- Wireframe S1 figures (`plot_fs_uh`, `plot_lookup_latency`,
  `plot_write_throughput`, `plot_adaptation_times`, `plot_s1_all`) over
  `PhaseSummary` and `PhaseAdaptation` objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .adaptation import PhaseAdaptation  # noqa: E402
from .aggregate import PhaseSummary, Summary  # noqa: E402

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


def _plot_phase_metric(summaries: Sequence[PhaseSummary], metric: str, ax: plt.Axes, by_phase: bool = True) -> None:
    rows = [s for s in summaries if s.metric == metric and not np.isnan(s.median)]
    cells = sorted({s.cell_id for s in rows})
    if not cells:
        ax.set_title(f"{metric} (no data)")
        return

    if by_phase:
        phases = sorted({s.phase for s in rows})
        width = 0.8 / max(len(phases), 1)
        for pi, phase in enumerate(phases):
            xs, ys, yerr_low, yerr_high = [], [], [], []
            for ci, cell in enumerate(cells):
                match = [s for s in rows if s.cell_id == cell and s.phase == phase]
                if not match:
                    continue
                s = match[0]
                xs.append(ci + pi * width)
                ys.append(s.median)
                yerr_low.append(max(s.median - s.ci95_low, 0.0))
                yerr_high.append(max(s.ci95_high - s.median, 0.0))
            ax.bar(xs, ys, width=width, label=f"Phase {phase}", yerr=[yerr_low, yerr_high], capsize=3)
        ax.set_xticks([i + width * (len(phases) - 1) / 2 for i in range(len(cells))])
        ax.set_xticklabels(cells)
        ax.legend()
    else:
        xs = list(range(len(cells)))
        ys, yerr_low, yerr_high = [], [], []
        for cell in cells:
            s = [r for r in rows if r.cell_id == cell][0]
            ys.append(s.median)
            yerr_low.append(max(s.median - s.ci95_low, 0.0))
            yerr_high.append(max(s.ci95_high - s.median, 0.0))
        ax.bar(xs, ys, yerr=[yerr_low, yerr_high], capsize=3, color=MARK)
        ax.set_xticks(xs)
        ax.set_xticklabels(cells)

    ax.set_xlabel("Cell")
    ax.set_ylabel(metric)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_fs_uh(summaries: Sequence[PhaseSummary], output_path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    _plot_phase_metric(summaries, "Fs", ax=axes[0], by_phase=False)
    axes[0].set_title("Fig 1a. Structural Fidelity (Fs) by cell")
    axes[0].set_ylim(0, 1.15)
    _plot_phase_metric(summaries, "Uh", ax=axes[1], by_phase=False)
    axes[1].set_title("Fig 1b. Effective Hash Coverage (Uh) by cell")
    axes[1].set_ylim(0, 1.15)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_lookup_latency(summaries: Sequence[PhaseSummary], output_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.8))
    _plot_phase_metric(summaries, "lat_p95_ms", ax=ax, by_phase=True)
    ax.set_title("Fig 2. Lookup latency (p95, ms) by phase")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_write_throughput(summaries: Sequence[PhaseSummary], output_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.8))
    _plot_phase_metric(summaries, "tps_write", ax=ax, by_phase=True)
    ax.set_title("Fig 3. Write throughput (tps) by phase")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_adaptation_times(
    adaptations: Sequence[PhaseAdaptation],
    output_path: Path,
    cells: tuple[str, str] = ("B", "D"),
) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.8))
    relevant = [a for a in adaptations if a.cell_id in cells]
    transitions = sorted({a.transition_no for a in relevant}) or [1, 2]
    width = 0.8 / max(len(cells), 1)

    for ci, cell in enumerate(cells):
        xs, ys = [], []
        for ti, transition in enumerate(transitions):
            vals = [
                a.adaptation_time_s
                for a in relevant
                if a.cell_id == cell and a.transition_no == transition
                and a.stabilized and a.adaptation_time_s is not None
            ]
            xs.append(ti + ci * width)
            ys.append(float(np.median(vals)) if vals else 0.0)
        ax.bar(xs, ys, width=width, label=f"Cell {cell}")

    ax.set_xticks([i + width / 2 for i in range(len(transitions))])
    ax.set_xticklabels([f"Transition {t}" for t in transitions])
    ax.set_ylabel("Adaptation time (s)")
    ax.set_title("Fig 4. AHI adaptation time per transition (cells B, D)")
    ax.legend()
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_s1_all(
    summaries: Sequence[PhaseSummary],
    adaptations: Sequence[PhaseAdaptation],
    output_dir: Path,
) -> dict[str, Path]:
    """Generate all four Wireframe S1 figures into output_dir/*.png."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "fs_uh": plot_fs_uh(summaries, output_dir / "fs_uh_by_cell.png"),
        "lookup_latency": plot_lookup_latency(summaries, output_dir / "lookup_latency_by_phase.png"),
        "write_throughput": plot_write_throughput(summaries, output_dir / "write_throughput_by_phase.png"),
        "adaptation_times": plot_adaptation_times(adaptations, output_dir / "ahi_adaptation_time.png"),
    }
