"""Mackie Control (MCU) over virtual MIDI - the only way into Fairlight's faders.

Resolve's Python API cannot write volume automation and no project export format
carries it, but Resolve will happily accept a control surface.  So we pretend to
be one: eight faders per bank, 14-bit, addressed as pitch-bend on MIDI channels
0-7, with a note-on per fader standing in for a finger touching it.
"""
from __future__ import annotations
import time
import mido

FADER_TOUCH = 104      # note 104..111 = fader 1..8; velocity 127 down, 0 up
PLAY, STOP = 94, 93
BANK_LEFT, BANK_RIGHT = 46, 47
CHAN_LEFT, CHAN_RIGHT = 48, 49
BANK_SIZE = 8

MIN_PITCH, MAX_PITCH = -8192, 8191

# Measured on Resolve Studio 21 (Touch=Latch, Write mode).  The taper is the
# standard Mackie one: roughly 435 pitch units per dB near unity, expanding to
# -inf at the bottom of the throw.
CALIBRATION = [(-8192, -140.0), (-6000, -48.0), (-4000, -30.0), (-2000, -16.0),
               (0, -8.5), (2000, -3.9), (3744, 0.0), (8000, 9.9)]


def db_to_pitch(db: float) -> int:
    """Invert the measured fader taper.  -inf (or anything below the throw) pins
    the fader fully closed."""
    if db <= -140: return MIN_PITCH
    pts = CALIBRATION
    if db <= pts[0][1]: return pts[0][0]
    if db >= pts[-1][1]: return pts[-1][0]
    for (p0, d0), (p1, d1) in zip(pts, pts[1:]):
        if d0 <= db <= d1:
            f = 0.0 if d1 == d0 else (db - d0) / (d1 - d0)
            return int(round(p0 + f * (p1 - p0)))
    return pts[-1][0]


class Surface:
    """A write-only MCU surface.  Resolve echoes fader positions back on its own
    MIDI output; reading them is optional and only used by `doctor`."""

    def __init__(self, out_name: str, in_name: str | None = None):
        self.out = mido.open_output(out_name)
        self.inp = mido.open_input(in_name) if in_name else None

    # ---- transport -------------------------------------------------------
    def press(self, note: int, hold: float = 0.05, after: float = 0.25) -> None:
        self.out.send(mido.Message("note_on", channel=0, note=note, velocity=127))
        time.sleep(hold)
        self.out.send(mido.Message("note_on", channel=0, note=note, velocity=0))
        time.sleep(after)

    def play(self):  self.press(PLAY, after=0.0)
    def stop(self):  self.press(STOP, after=0.0)

    def select_bank(self, bank: int) -> None:
        """Bank `bank` puts timeline tracks 8*bank+1 .. 8*bank+8 on faders 1..8."""
        for _ in range(12): self.press(BANK_LEFT, after=0.10)   # home
        for _ in range(bank): self.press(BANK_RIGHT, after=0.25)
        time.sleep(0.4)

    # ---- faders ----------------------------------------------------------
    def touch(self, ch: int, down: bool = True) -> None:
        self.out.send(mido.Message("note_on", channel=0, note=FADER_TOUCH + ch,
                                   velocity=127 if down else 0))

    def set(self, ch: int, pitch: int) -> None:
        self.out.send(mido.Message("pitchwheel", channel=ch,
                                   pitch=max(MIN_PITCH, min(MAX_PITCH, int(pitch)))))

    def read_positions(self, settle: float = 0.4) -> dict[int, int]:
        if not self.inp: return {}
        pos = {}
        t0 = time.time()
        while time.time() - t0 < settle:
            for m in self.inp.iter_pending():
                if m.type == "pitchwheel": pos[m.channel] = m.pitch
            time.sleep(0.005)
        return pos

    def close(self) -> None:
        try: self.stop()
        except Exception: pass
        for ch in range(BANK_SIZE):
            try: self.touch(ch, False)
            except Exception: pass
        time.sleep(0.15)
        self.out.close()
        if self.inp: self.inp.close()

    def __enter__(self): return self
    def __exit__(self, *e): self.close()
