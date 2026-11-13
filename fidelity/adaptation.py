"""AHI adaptation-time detector -- FR-14.

Given a time series sampled during an AHI-on run (typically Uh per sampling
interval, or median lookup latency), the adaptation time is the first time t
after which the series stays inside a tolerance band around its plateau.

    plateau  = median of the last W samples
    adapted  = first index i such that every sample from i to the end
               satisfies |x_j - plateau| <= epsilon,
               and at least W samples remain from i onward

If no such i exists the run is reported as not adapted (None). W (stability
window, in samples) and epsilon (absolute tolerance, same unit as the series)
are parameters to be fixed after the pilot runs; the defaults below are
placeholders only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

DEFAULT_WINDOW = 5
DEFAULT_EPSILON = 0.05


@dataclass(frozen=True)
class Adaptation:
    adapted: bool
    time: Optional[float]      # timestamp of adaptation, or None
    index: Optional[int]
    plateau: float
    window: int
    epsilon: float


def detect_adaptation(times: Sequence[float], values: Sequence[float],
                      window: int = DEFAULT_WINDOW,
                      epsilon: float = DEFAULT_EPSILON) -> Adaptation:
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
