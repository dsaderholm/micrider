"""Decide, from the recorded audio alone, when each performer is actually speaking.

The whole problem is bleed: on a 16-mic musical every mic hears every line.  What
separates a performer's own line from the rest of the stage is that it is much
louder on their mic than on the median of all the others.  That single test does
most of the work; the rest is hysteresis so quiet onsets and tails survive.
"""
from __future__ import annotations
import os, subprocess
import numpy as np

SR, BIN_SAMPLES = 8000, 400          # 8 kHz, 50 ms bins
BIN = BIN_SAMPLES / SR


def envelope(path: str, start: float = 0.0, dur: float | None = None) -> np.ndarray:
    """50 ms RMS envelope of one file, in dBFS."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}"]
    if dur: cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    n = (len(x) // BIN_SAMPLES) * BIN_SAMPLES
    if n == 0: return np.zeros(0, np.float32)
    e = np.sqrt((x[:n].reshape(-1, BIN_SAMPLES) ** 2).mean(axis=1)) + 1e-9
    return (20 * np.log10(e)).astype(np.float32)


def rolling_quantile(E: np.ndarray, window: float, q: float, hop: float = 2.0) -> np.ndarray:
    """Slow-moving low quantile of each row - a per-mic noise floor that follows
    the room as it changes (audience, HVAC, a pack that starts rustling)."""
    w, h = int(window / BIN), int(hop / BIN)
    n = E.shape[1]
    idx = np.arange(0, max(1, n - w) + 1, h)
    out = np.empty((E.shape[0], len(idx)), np.float32)
    for k, s in enumerate(idx):
        out[:, k] = np.quantile(E[:, s:s + w], q, axis=1)
    t, tk = np.arange(n), idx + w / 2
    return np.vstack([np.interp(t, tk, out[i]) for i in range(E.shape[0])]).astype(np.float32)


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    ii = np.flatnonzero(flags)
    if not len(ii): return []
    out, a, p = [], ii[0], ii[0]
    for j in ii[1:]:
        if j - p > 1: out.append((a, p)); a = j
        p = j
    out.append((a, p))
    return out


class Show:
    """Envelopes for every mic plus the derived masks, cached to one .npz."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.names = [cfg.tracks[k] for k in sorted(cfg.tracks)]
        self.numbers = sorted(cfg.tracks)

    def build(self, cache: str | None = None, verbose: bool = True) -> "Show":
        c = self.cfg
        dur = (c.window[1] - c.window[0]) or None
        mats = []
        for nm in self.names:
            e = envelope(c.path(nm), c.offset + c.window[0], dur)
            mats.append(e)
            if verbose:
                print(f"  {nm:<16} {len(e):>7} bins  peak {e.max():6.1f}  "
                      f"median {np.median(e):6.1f} dBFS", flush=True)
        n = min(len(e) for e in mats)
        self.raw = np.vstack([e[:n] for e in mats])
        self.prog = (envelope(c.path(c.program), c.offset + c.window[0], dur)[:n]
                     if c.program else None)
        if cache:
            np.savez_compressed(cache, raw=self.raw,
                                prog=self.prog if self.prog is not None else np.zeros(0))
        return self.derive()

    def load(self, cache: str) -> "Show":
        z = np.load(cache)
        self.raw = z["raw"]
        self.prog = z["prog"] if z["prog"].size else None
        return self.derive()

    def derive(self) -> "Show":
        c, d = self.cfg, self.cfg.detect
        g = np.array([c.gain(n) for n in self.names], np.float32)[:, None]
        self.M = self.raw + g                        # as it sounds in the timeline
        self.D = self.M - np.median(self.M, axis=0)  # dominance over the pack
        amb = rolling_quantile(self.raw, d.ambient_window, d.ambient_q) + d.ambient_offset
        self.A = self.M - amb                        # each mic against its own floor
        self.n = self.M.shape[1]
        self.mask = np.zeros(self.n, bool)
        self.songs = []
        if self.prog is not None:
            p = self.prog + c.gain(c.program)
            loud = p > np.percentile(p, 10) + d.song_over
            for a, b in _runs(loud):
                if (b - a) * BIN >= d.song_min: self.songs.append((a, b))
            pad = int(d.song_pad / BIN)
            for a, b in self.songs:
                self.mask[max(0, a - pad):min(self.n, b + pad)] = True
        return self

    def regions(self, i: int) -> list[tuple[float, float]]:
        """Speech regions for row i, in timeline seconds."""
        d = self.cfg.detect
        D, A, M, mask = self.D[i], self.A[i], self.M[i], self.mask
        strong = (D > d.dominance) & (A > d.ambient) & (M > d.floor) & (~mask)
        keep = np.zeros_like(strong)
        for a, b in _runs(strong):                   # a trigger must sustain
            if (b - a + 1) * BIN >= d.sustain: keep[a:b + 1] = True
        strong = keep
        weak = (D > d.weak_dominance) & (M > d.weak_floor) & (~mask)
        sel = strong.copy()
        grow = int(d.grow / BIN)
        for a, b in _runs(weak):                     # grow outward into the quiet edges
            st = np.flatnonzero(strong[a:b + 1])
            if not len(st): continue
            sel[max(a, a + st[0] - grow):min(b, a + st[-1] + grow) + 1] = True
        r = _runs(sel)
        if not r: return []
        out = [list(r[0])]
        for a, b in r[1:]:                           # one open region per exchange
            if a - out[-1][1] <= int(d.merge / BIN): out[-1][1] = max(out[-1][1], b)
            else: out.append([a, b])
        off = self.cfg.window[0]
        return [(off + a * BIN, off + b * BIN) for a, b in out
                if (b - a) >= int(d.min_len / BIN)]
