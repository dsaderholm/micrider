"""Configuration: one TOML file describes a show."""
from __future__ import annotations
import json, os, tomllib
from dataclasses import dataclass, field, asdict


@dataclass
class Shape:
    """The trapezoid written for each speech region, and how it is driven.

    Defaults come from theatre practice: a mic opens slightly before the line so
    no consonant is clipped, and closes shortly after so the next line's bleed
    does not arrive through an open channel.
    """
    open_db: float = 0.0        # plateau level
    pre: float = 0.35           # fully open this long before speech starts
    ramp_in: float = 0.10       # fade-up duration
    hold: float = 0.55          # stay open this long after speech ends
    ramp_out: float = 0.30      # fade-down duration
    lead: float = 0.15          # sent early, to cancel Resolve's write latency
    step: int = 1400            # pitch units between sends; larger = fewer points


@dataclass
class Detect:
    """Thresholds for deciding that a mic is on its own wearer's voice.

    `dominance` is the key one: a real line is far louder on that performer's
    own mic than on the median of all the others, while bleed is not.
    """
    dominance: float = 14.0     # dB above the median of all mics
    ambient: float = 8.0        # dB above this mic's own noise floor
    floor: float = -60.0        # absolute dBFS gate
    sustain: float = 0.25       # a trigger must last this long
    weak_dominance: float = 6.0 # looser test used only to extend a region
    weak_floor: float = -48.0
    grow: float = 0.75          # extend at most this far past the loud part
    merge: float = 1.5          # lines closer than this become one open region
    min_len: float = 0.25
    song_over: float = 15.0     # program track this far above its own floor = a song
    song_min: float = 10.0      # ... for at least this long
    song_pad: float = 2.0
    ambient_window: float = 30.0
    ambient_q: float = 0.15
    ambient_offset: float = 2.7


@dataclass
class Config:
    audio_dir: str
    tracks: dict[int, str]              # Fairlight track number -> wav file
    program: str | None = None          # backing-track reference, for song masking
    offset: float = 0.0                 # source seconds sitting at timeline start
    window: tuple[float, float] = (0.0, 0.0)   # timeline seconds; (0,0) = whole timeline
    gains: dict[str, float] = field(default_factory=dict)
    midi_out: str = "Claude 1"
    midi_in: str | None = None
    shape: Shape = field(default_factory=Shape)
    detect: Detect = field(default_factory=Detect)

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path, "rb") as f:
            d = tomllib.load(f)
        a, t = d.get("audio", {}), d.get("timeline", {})
        files = t.get("tracks", [])
        first = int(t.get("first_track", 1))
        tracks = {int(k): v for k, v in t.get("map", {}).items()} or \
                 {first + i: f for i, f in enumerate(files)}
        gains = d.get("gains", {})
        if isinstance(gains, str):                     # a path to gains.json
            gains = json.load(open(os.path.join(os.path.dirname(path), gains)))
        w = a.get("window", [0.0, 0.0])
        return cls(
            audio_dir=a["dir"], tracks=tracks, program=a.get("program"),
            offset=float(a.get("offset", 0.0)), window=(float(w[0]), float(w[1])),
            gains=gains,
            midi_out=d.get("midi", {}).get("out", "Claude 1"),
            midi_in=d.get("midi", {}).get("in"),
            shape=Shape(**d.get("shape", {})), detect=Detect(**d.get("detect", {})),
        )

    def path(self, name: str) -> str:
        return os.path.join(self.audio_dir, name)

    def gain(self, name: str) -> float:
        return float(self.gains.get(name, 0.0))

    def describe(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)
