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

# Resolve will not act on transport or fader messages until it believes a surface
# is really there.  It asks once a second with a MIDI Identity Request and waits
# for the reply every hardware MCU sends.  Without it Resolve transmits happily -
# so the wiring looks correct and `doctor` passes - while ignoring everything it
# receives, and a pass dies with "the transport never started".
IDENTITY_REQUEST = (0x7E, 0x00, 0x06, 0x01)
IDENTITY_REPLY = (0x7E, 0x00, 0x06, 0x02,       # sysex non-realtime, identity reply
                  0x00, 0x00, 0x66,             # Mackie
                  0x14,                         # Mackie Control
                  1, 2, 3, 4, 5, 6, 7)          # serial; any value is accepted
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

    def __init__(self, out_name: str, in_name: str | None = None,
                 handshake: float = 4.0):
        self.out = mido.open_output(out_name)
        self.inp = mido.open_input(in_name) if in_name else None
        if self.inp and handshake:
            self.handshake(handshake)

    # ---- identity --------------------------------------------------------
    def answer_identity(self) -> int:
        """Reply to any pending identity request.  Cheap; call it in a loop."""
        if not self.inp:
            return 0
        n = 0
        for m in self.inp.iter_pending():
            if m.type == "sysex" and tuple(m.data[:4]) == IDENTITY_REQUEST:
                self.out.send(mido.Message("sysex", data=IDENTITY_REPLY))
                n += 1
        return n

    def handshake(self, timeout: float = 4.0) -> bool:
        """Wait for Resolve's identity request and answer it.

        Returns False if none arrived, which means Resolve is not transmitting
        at all - a real wiring or protocol problem rather than a missing reply.
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.answer_identity():
                time.sleep(0.2)
                return True
            time.sleep(0.02)
        return False

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
