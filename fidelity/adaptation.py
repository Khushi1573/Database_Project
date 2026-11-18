"""AHI adaptation-time detector -- FR-14, AC-9.

Supports both:
1. `detect_adaptation(times, values, window, epsilon)` (plateau-band detector)
2. `detect_phase_adaptation(...)` following D.4.2 section 5.5:
   - Smooth post-transition series r = h/(h+b) with moving median (window=5)
   - Scan forward for first index i such that max(r[i:i+W]) - min(r[i:i+W]) <= eps
   - Return censored (`stabilized=False`, `adaptation_time_s=None`) if not
     stabilized within the phase.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

DEFAULT_WINDOW = 10
DEFAULT_EPSILON = 0.02
DEFAULT_MEDIAN_WINDOW = 5


@dataclass(frozen=True)
class Adaptation:
    adapted: bool
    time: Optional[float]      # timestamp of adaptation, or None
    index: Optional[int]
    plateau: float
    window: int
    epsilon: float


@dataclasses.dataclass
class PhaseAdaptation:
    cell_id: str
    transition_no: int          # 1 = phase1->phase2, 2 = phase2->phase3
    transition_t: float
    stabilized: bool
    adaptation_time_s: Optional[float]
    steady_value: Optional[float]
    median_window: int
    stabilization_window: int
    epsilon: float
    method: str
    detail: str


def moving_median(values: Sequence[float], window: int = DEFAULT_MEDIAN_WINDOW) -> list[float]:
    """Trailing moving median: output[i] = median(values[max(0,i-window+1):i+1])."""
    if window < 1:
        raise ValueError("moving median window must be >= 1")
    arr = np.asarray(values, dtype=float)
    return [float(np.median(arr[max(0, i - window + 1) : i + 1])) for i in range(len(arr))]


def detect_adaptation(times: Sequence[float], values: Sequence[float],
                      window: int = 5,
                      epsilon: float = 0.05) -> Adaptation:
    t = np.asarray(times, dtype=float)
    x = np.asarray(values, dtype=float)
    if t.shape != x.shape or t.ndim != 1:
        raise ValueError("times and values must be 1-D and the same length")
    if window < 1 or epsilon < 0:
        raise ValueError("window must be >= 1 and epsilon >= 0")
    if np.any(np.diff(t) < 0):
        raise ValueError("times must be non-decreasing")
    if len(x) < window:
        return Adaptation(False, None, None, float("nan"), window, epsilon)

    plateau = float(np.median(x[-window:]))
    outside = np.abs(x - plateau) > epsilon
    if outside[-1]:
        return Adaptation(False, None, None, plateau, window, epsilon)

    # index right after the last sample that was outside the band
    last_out = np.flatnonzero(outside)
    i = int(last_out[-1]) + 1 if last_out.size else 0
    if len(x) - i < window:
        return Adaptation(False, None, None, plateau, window, epsilon)
    return Adaptation(True, float(t[i]), i, plateau, window, epsilon)


def detect_phase_adaptation(
    t_rel_s: Sequence[float],
    values: Sequence[float],
    transition_t: float,
    cell_id: str = "",
    transition_no: int = 1,
    *,
    median_window: int = DEFAULT_MEDIAN_WINDOW,
    stabilization_window: int = DEFAULT_WINDOW,
    epsilon: float = DEFAULT_EPSILON,
    end_t: Optional[float] = None,
) -> PhaseAdaptation:
    """D.4.2 section 5.5 adaptation-time detector on a post-transition window."""
    if len(t_rel_s) != len(values):
        raise ValueError("t_rel_s and values must have the same length")
    if stabilization_window < 1 or epsilon < 0:
        raise ValueError("stabilization_window must be >= 1 and epsilon >= 0")

    method = f"moving_median({median_window})+range_band(W={stabilization_window},eps={epsilon})"

    post_pairs = [
        (float(t), float(v))
        for t, v in zip(t_rel_s, values)
        if t >= transition_t and (end_t is None or t <= end_t)
    ]
    if len(post_pairs) < stabilization_window:
        return PhaseAdaptation(
            cell_id=cell_id,
            transition_no=transition_no,
            transition_t=transition_t,
            stabilized=False,
            adaptation_time_s=None,
            steady_value=None,
            median_window=median_window,
            stabilization_window=stabilization_window,
            epsilon=epsilon,
            method=method,
            detail="censored: insufficient post-transition samples",
        )

    post_t = [p[0] for p in post_pairs]
    smoothed = moving_median([p[1] for p in post_pairs], window=median_window)
    steady = float(np.median(smoothed[-stabilization_window:]))

    for i in range(len(smoothed) - stabilization_window + 1):
        window_slice = smoothed[i : i + stabilization_window]
        if max(window_slice) - min(window_slice) <= epsilon:
            elapsed = max(0.0, post_t[i] - transition_t)
            return PhaseAdaptation(
                cell_id=cell_id,
                transition_no=transition_no,
                transition_t=transition_t,
                stabilized=True,
                adaptation_time_s=round(elapsed, 3),
                steady_value=steady,
                median_window=median_window,
                stabilization_window=stabilization_window,
                epsilon=epsilon,
                method=method,
                detail=f"stabilized at t={post_t[i]:.2f}s (dt={elapsed:.2f}s)",
            )

    return PhaseAdaptation(
        cell_id=cell_id,
        transition_no=transition_no,
        transition_t=transition_t,
        stabilized=False,
        adaptation_time_s=None,
        steady_value=steady,
        median_window=median_window,
        stabilization_window=stabilization_window,
        epsilon=epsilon,
        method=method,
        detail="censored: did not stabilize within phase",
    )
