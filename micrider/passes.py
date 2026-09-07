"""The real-time write pass.

Fairlight only records automation while the timeline is rolling, so a pass costs
exactly as long as the material.  Eight faders move at once, so sixteen mics take
two passes rather than sixteen.

The pass plays straight through from one end of the window to the other.  Earlier
versions seeked to each line and played only the interesting parts; every one of
them produced clipped entrances, because Latch needs a moment to engage after the
transport starts and a seek puts that moment right where the line begins.
"""
from __future__ import annotations
import time
import numpy as np

from .mcu import Surface, BANK_SIZE
from .shape import GRID


class Stalled(RuntimeError):
    pass


def banks_for(track_numbers) -> dict[int, dict[int, int]]:
    """Group Fairlight track numbers into MCU banks: {bank: {fader channel: track}}."""
    out: dict[int, dict[int, int]] = {}
    for t in sorted(track_numbers):
        out.setdefault((t - 1) // BANK_SIZE, {})[(t - 1) % BANK_SIZE] = t
    return out


def write_bank(clock, surface: Surface, paths: dict[int, np.ndarray],
               bank: int, t0: float, t1: float, shape,
               log=print, log_every: float = 60.0) -> int:
    """Drive one bank of eight faders from t0 to t1 while the timeline rolls.

    `paths` maps fader channel (0-7) to a fader path on the 10 ms grid.  Channels
    with no path are left alone - their tracks are not part of this job.
    """
    surface.select_bank(bank)
    clock.seek(t0)
    time.sleep(0.8)
    origin = clock.now()

    chans = sorted(paths)
    for ch in chans: surface.touch(ch, True)        # Latch engages on touch
    time.sleep(0.10)
    last = {}
    for ch in chans:
        v = int(paths[ch][int(t0 / GRID)])
        surface.set(ch, v); last[ch] = v; time.sleep(0.01)
    time.sleep(0.5)

    surface.play()
    started = time.time()
    rolling = False
    t = t_seen = t0
    wall_seen = time.time()
    nudges = sent = 0
    next_log = t0 + log_every

    while True:
        try:
            t = t0 + (clock.now() - origin)
        except Exception:
            time.sleep(0.2); continue

        if not rolling:
            if t > t0 + 0.05:
                rolling = True; started = time.time()
            elif time.time() - started > 4.0:
                raise Stalled("the transport never started")

        # A stalled transport is the dangerous failure: Write keeps recording, so
        # it would lay a flat line over everything from that point on.
        if t > t_seen + 0.02:
            t_seen, wall_seen, nudges = t, time.time(), 0
        elif time.time() - wall_seen > 3.0:
            if nudges < 2:
                nudges += 1
                log(f"   !! transport stalled at {t / 60:.2f} min - nudging play")
                surface.play(); wall_seen = time.time()
            else:
                raise Stalled(f"transport stalled at {t:.1f}s "
                              f"(resume this bank from {t:.0f})")

        if t >= t1: break
        j = int((t + shape.lead) / GRID)
        if j >= len(paths[chans[0]]): break
        for ch in chans:
            v = int(paths[ch][j])
            # only two values ever exist, so this is one message per edge and
            # Resolve records exactly two keyframes for it
            if v != last[ch]:
                surface.set(ch, v); last[ch] = v; sent += 1
        if t >= next_log:
            log(f"   {t / 60:6.2f} min / {t1 / 60:.1f}   "
                f"drift {(time.time() - started) - (t - t0):+.2f}s   {sent} moves")
            next_log += log_every
        time.sleep(0.02)
    return sent
