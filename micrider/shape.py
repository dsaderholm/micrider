"""Turn speech regions into the path the fader should travel.

One rectangle per region - open, hold, closed - sampled onto a fine grid so that
overlapping regions merge by taking the maximum.  Two lines close together then
produce one continuous open span with no dip, which is what a human mixer does
and also what keeps the point count down.

There are no fades, by design.  See `Shape`.
"""
from __future__ import annotations
import numpy as np
from .mcu import db_to_pitch, MIN_PITCH

GRID = 0.01          # 10 ms


def fader_path(regions, duration: float, shape) -> np.ndarray:
    """Fader position in MCU pitch units, on a 10 ms grid, for one track."""
    n = int(duration / GRID) + 400
    closed, open_ = MIN_PITCH, db_to_pitch(shape.open_db)
    path = np.full(n, float(closed))
    for a, b in regions:
        i0 = int(np.clip(round((a - shape.pre) / GRID), 0, n - 1))
        i1 = int(np.clip(round((b + shape.hold) / GRID), 0, n - 1))
        if i1 > i0:
            path[i0:i1] = open_
    return path


def open_fraction(path: np.ndarray) -> float:
    return float((path > MIN_PITCH + 100).mean())
