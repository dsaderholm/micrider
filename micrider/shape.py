"""Turn speech regions into the path the fader should travel.

One trapezoid per region - up, hold, down - sampled onto a fine grid so that
overlapping regions merge by taking the maximum.  Two lines close together then
produce one continuous open span with no dip, which is what a human mixer does
and also what keeps the point count down.
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
        marks = (a - shape.pre - shape.ramp_in, a - shape.pre,
                 b + shape.hold, b + shape.hold + shape.ramp_out)
        i0, i1, i2, i3 = [int(np.clip(round(t / GRID), 0, n - 1)) for t in marks]
        if i1 > i0:
            path[i0:i1] = np.maximum(path[i0:i1], np.linspace(closed, open_, i1 - i0))
        if i2 > i1:
            path[i1:i2] = np.maximum(path[i1:i2], open_)
        if i3 > i2:
            path[i2:i3] = np.maximum(path[i2:i3], np.linspace(open_, closed, i3 - i2))
    return path


def open_fraction(path: np.ndarray) -> float:
    return float((path > MIN_PITCH + 100).mean())
