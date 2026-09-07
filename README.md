# micrider

Writes **DaVinci Resolve Fairlight track fader automation** for lavalier mics,
from the recorded audio.

Mixing a stage musical means riding sixteen mic faders line by line: up as a
performer speaks, down the moment they stop, for three hours. micrider listens to
the multitrack, works out who is actually talking, and writes real automation into
the timeline — ordinary keyframes on the track's Fader Level lane, which you can
then drag, reshape or delete like anything you drew by hand.

It writes **track** automation, not clip gain, and not a gate. That distinction is
the whole point: a gate cannot be edited afterwards, and clip gain is not where a
mix lives.

---

## Why this is harder than it sounds

Resolve's Python API cannot write volume automation. Neither `.drt` nor `.drp`
exports carry it. There is no file to edit and no API call to make.

What Resolve *will* do is accept a **control surface**. So micrider pretends to be
one — a Mackie Control (MCU) on a virtual MIDI port — and mixes the show the way a
person would, in real time, moving eight faders at once. Sixteen mics take two
passes, each as long as the act.

---

## Setup

**1. A virtual MIDI cable, in two directions**

Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
(`winget install TobiasErichsen.loopMIDI`) and create **two** ports, for example
`Claude 1` and `Claude B 1`. Two are required: with one, Resolve hears its own
output and the MCU state machine loops forever. Neither may be muted — loopMIDI's
round button toggles that.

**2. Point Resolve at them**

`Preferences > Control Panels > MIDI`:

| setting | value |
|---|---|
| Use MIDI audio console | on |
| Protocol | **MCU compatible** |
| MIDI Input | port A — the one micrider sends to |
| MIDI Output | port B |

**3. Enable external scripting**

`Preferences > System > General > External scripting using` set to Local.

**4. Install**

```
pip install -e .
```

**5. The one setting that will waste your night if you miss it**

On the Fairlight page's automation toolbar there is a **Touch** group:
`Off / Latch / Snap / Snap Latch`.

> **If Touch is `Off`, Resolve accepts fader messages and silently does nothing
> with them.** No error, no warning; the fader simply never moves. Set it to
> **Latch**. It resets to `Off` every time Resolve restarts.

Also: mode **Write** (or Latch), **Enables > Fader** lit, **On Stop > Hold**, and
every track you are writing set to Latch, Write or Global.

`micrider doctor` prints this checklist along with what it can actually see.

---

## Use

```
micrider -c show.toml doctor
micrider -c show.toml gains -o gains.json
micrider -c show.toml plan
micrider -c show.toml write
micrider -c show.toml write --bank 0 --start 600 --end 900
```

`plan` is free and instant after the first analysis — run it, read the region
counts, and only then spend the real time on `write`.

A pass is destructive in the useful sense: Write mode replaces the whole fader
lane across the window it covers, so a bad earlier pass is repaired simply by
running a good one over the same range.

### The config

One TOML file describes a show. See [`examples/good-news.toml`](examples/good-news.toml).

```toml
[audio]
dir     = "E:/Good News/Audio"
program = "Tracks.wav"     # backing tracks; used to mask the musical numbers
offset  = 1718.12          # source seconds sitting at timeline start
window  = [0.0, 4385.0]    # the act, in timeline seconds

[timeline]
first_track = 1
tracks = ["Tom.wav", "Connie.wav", "Bob.wav"]   # A1, A2, A3, ...

[midi]
out = "Claude 1"

[gains]                    # clip gain already applied in the timeline
"Tom.wav" = -1.0
```

`offset` matters: thresholds are meaningless if the analysis is looking at a
different part of the recording than the timeline is. If the clips carry no usable
timecode, line one mic up by ear and measure it once.

---

## How it decides who is talking

Every mic hears every line — that is what makes this hard. What separates a
performer's own line from the bleed of the person next to them is **dominance**:
their line is far louder on their own mic than on the median of all the others,
while bleed is not.

1. **Envelope.** 50 ms RMS per mic, in dBFS, with clip gain folded in.
2. **Ambient.** A slow low quantile of each mic's own level — a noise floor that
   follows the room rather than a fixed number.
3. **Strong test.** More than `dominance` dB above the median of all mics, *and*
   above its own ambient, *and* above an absolute floor — sustained for 0.25 s, so
   a dropped prop does not open a mic.
4. **Grow.** From each strong hit, extend outward while the mic is still merely
   *above* the pack, so quiet onsets and dying tails survive.
5. **Merge.** Regions closer than 1.5 s become one open span. An exchange is one
   region, not eleven.
6. **Songs.** When the backing-track reference is loud, the range is masked out.
   Numbers are a different mixing problem and are left alone.

Each region then becomes one trapezoid: open `pre` seconds early over a `ramp_in`
fade, hold, and fade out over `ramp_out` starting `hold` seconds after the last
word. Overlapping trapezoids merge by maximum, so two close lines produce one
continuous open span instead of a dip.

Defaults: open 0.35 s early, 0.10 s fade up, hold 0.55 s, 0.30 s fade down. Levels
are binary — 0 dB open, minus infinity closed. A soft expander on the track handles
word-level detail; automation only needs to work at line level.

---

## Notes from getting this working

Things that cost real time, recorded so they cost nobody else any:

- **Touch = Off silently swallows everything.** Every strange symptom — values
  appearing to wrap, faders "pinned" at +10 or minus infinity, only channel 0
  responding, identical input giving different output — was this. Nothing was wrong
  with the MIDI.
- **All eight channels work.** Pitch-bend on MIDI channels 0-7 drives faders 1-8
  independently. If only channel 0 seems to respond, see above.
- **Fader touch is mandatory.** Note 104-111, velocity 127 down and 0 up. Without
  it Latch never engages, and Write lays a flat line that erases the lane.
- **Never seek mid-pass.** Playing only the interesting parts sounds efficient and
  clips every entrance, because Latch needs a moment after the transport starts and
  a seek puts that moment exactly where the line begins. Play straight through.
- **Sync to Resolve's playhead, not wall time.** `GetCurrentTimecode()` costs about
  0.4 ms. Measured drift over a 73-minute pass stayed under 0.1 s.
- **Compensate the write latency.** Automation lands about 0.11 s after the MIDI
  arrives, consistently. `shape.lead` sends that far ahead; measured residual error
  afterwards was -0.09 s with a standard deviation of 0.04 s.
- **Points cost what you send.** Resolve records roughly two keyframes per value
  change, so the ramp step size (`shape.step`) sets how cluttered the lane is. 1400
  pitch units gives about seven steps per fade — an angled line at any working zoom,
  and around twenty points per line.
- **A killed script never sends Stop**, so Resolve keeps rolling and Write keeps
  recording over everything. micrider always stops the transport on the way out, and
  treats a stalled transport as a hard failure for the same reason.
- **Ignore the device-inquiry SysEx.** Resolve sends `7E 00 06 01` forever and
  accepts no identity reply. Faders work regardless.
- **Verified fader mapping** (Touch = Latch, Write): pitch `3744` is exactly 0.0 dB,
  `0` is -8.5 dB, `8000` is +9.9 dB, `-8192` is minus infinity. About 435 units per
  dB near unity.

---

## Requirements

DaVinci Resolve **Studio** (free Resolve has no control-surface support), Python
3.11+, `ffmpeg` on PATH, and a virtual MIDI driver. Developed on Resolve Studio 21
on Windows; nothing in it is Windows-specific except the default path to Resolve's
scripting modules, which `RESOLVE_SCRIPT_MODULES` overrides.

## Licence

MIT.
