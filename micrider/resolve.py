"""Talking to Resolve itself: the playhead clock, and reading clip gain.

Only two things are needed from the scripting API.  One is the playhead, because
fader moves have to be placed against Resolve's own clock rather than wall time.
The other is clip gain, which shifts every level in the timeline and so has to be
folded into the analysis before any threshold means anything.
"""
from __future__ import annotations
import glob, os, re, struct, sys, zipfile

DEFAULT_MODULES = os.environ.get(
    "RESOLVE_SCRIPT_MODULES",
    r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules")


def connect():
    if DEFAULT_MODULES not in sys.path: sys.path.append(DEFAULT_MODULES)
    import DaVinciResolveScript as dvr           # noqa: E402
    app = dvr.scriptapp("Resolve")
    if app is None:
        raise RuntimeError("Resolve is not running, or external scripting is not "
                           "enabled (Preferences > System > General).")
    return app


def timeline():
    project = connect().GetProjectManager().GetCurrentProject()
    if project is None: raise RuntimeError("no project open")
    tl = project.GetCurrentTimeline()
    if tl is None: raise RuntimeError("no timeline open")
    return tl


class Clock:
    """Timeline seconds <-> drop-frame timecode, anchored at the timeline start.

    29.97 drop-frame skips two frame *labels* a minute, nine times in ten, so
    timecode arithmetic has to go through absolute frame numbers or a long pass
    slowly slides out of sync.
    """

    def __init__(self, tl, fps: float = 30000 / 1001.0):
        self.tl, self.fps = tl, fps
        self.start = int(tl.GetStartFrame())

    @staticmethod
    def tc_to_frames(tc: str) -> int:
        h, m, s, f = map(int, re.split(r"[:;]", tc))
        mins = h * 60 + m
        return (h * 3600 + m * 60 + s) * 30 + f - 2 * (mins - mins // 10)

    @staticmethod
    def frames_to_tc(fr: int) -> str:
        d, rem = fr // 17982, fr % 17982
        f2 = fr + 18 * d + 2 * ((rem - 2) // 1798 if rem >= 2 else 0)
        return (f"{f2 // 108000:02d}:{(f2 % 108000) // 1800:02d}:"
                f"{(f2 % 1800) // 30:02d};{f2 % 30:02d}")

    def seek(self, seconds: float) -> None:
        self.tl.SetCurrentTimecode(
            self.frames_to_tc(self.start + int(round(seconds * self.fps))))

    def now(self) -> float:
        return (self.tc_to_frames(self.tl.GetCurrentTimecode()) - self.start) / self.fps

    def duration(self) -> float:
        return (int(self.tl.GetEndFrame()) - self.start) / self.fps


def read_clip_gains(tl, workdir: str) -> dict[str, float]:
    """Clip gain per audio clip, in dB.

    Resolve exposes no API for this, but a .drt export is a zip of XML and the
    value survives inside a hex-encoded blob: an IEEE-754 double immediately
    after the marker 0b0a0911 in each clip's EffectFiltersBA.
    """
    os.makedirs(workdir, exist_ok=True)
    drt = os.path.join(workdir, "_gains.drt")
    app = connect()
    if not tl.Export(drt, app.EXPORT_DRT, app.EXPORT_NONE):
        raise RuntimeError("timeline export failed")
    xdir = os.path.join(workdir, "_gains_x")
    with zipfile.ZipFile(drt) as z: z.extractall(xdir)
    biggest = max(glob.glob(os.path.join(xdir, "SeqContainer", "*.xml")),
                  key=os.path.getsize)
    text = open(biggest, encoding="utf-8", errors="ignore").read()
    gains: dict[str, float] = {}
    for m in re.finditer(r"<Sm2TiAudioClip\b.*?</Sm2TiAudioClip>", text, re.S):
        blk = m.group(0)
        name = re.search(r"<Name>([^<]*)</Name>", blk)
        blob = re.search(r"<EffectFiltersBA>([0-9a-fA-F]*)</EffectFiltersBA>", blk)
        if not (name and blob): continue
        h = blob.group(1); j = h.find("0b0a0911")
        if j < 0: continue
        gains[name.group(1)] = round(struct.unpack("<d", bytes.fromhex(h[j + 8:j + 24]))[0], 3)
    return gains
