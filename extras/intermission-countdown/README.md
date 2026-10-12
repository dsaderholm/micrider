# Intermission countdown: *Good News!*

A one-minute intermission card for the recording of Intermountain Christian
School Theatre Conservatory's mainstage musical *Good News!*. It is built from
the show poster (`source/poster.jpg`).

**Output:** `renders/good-news-intermission-1080p2997.mp4`. 1920×1080, 29.97 fps,
H.264 High 4:2:0, Rec.709, no audio, 63 s (1890 frames).

| Timecode    | What happens                                                     |
|-------------|------------------------------------------------------------------|
| 00:00:00:00 | Fades up from black. The title builds on: arch, letters, arrow, mascot |
| 00:00:01:00 | The clock reads **1:00** and starts counting                     |
| 00:01:01:00 | The clock reads **0:00**. Ball through the uprights, mascot jumps |
| 00:01:02:00 | Fades to black                                                   |
| 00:01:03:00 | End                                                              |

The clock ticks once every 30 frames, so on a 29.97 timeline every tick lands
on a timecode second. On a non-drop-frame timeline the clip runs 1:03:00 and the
countdown runs exactly 1:00:00 of it. Trim the head to cut straight in on 1:00,
or the tail to cut out on 0:00 before the fade.

## What moves

- The sunburst turns slowly and music notes drift up behind everything.
- GOOD NEWS! pops in letter by letter. The arrow flies in and goes through the
  heart. After that the heart beats every few seconds and the "!" sways.
- The football mascot hops on and sways to the music.
- The INTERMISSION pennant swings down and flutters.
- The digits roll on each tick and pulse for the last ten seconds.
- A football runs the length of the yard-line bar in step with the clock.

## Rebuilding

You only need this to change something. Python 3 with numpy, scipy, opencv
and Pillow; Node with Playwright; ffmpeg.

```sh
python3 build_assets.py --scale 0.85 --out assets/1080   # cut the art out of the poster
node render.js --stills 1.5,30,61.3                      # check a few frames
node render.js                                           # full render, a few minutes
```

- `build_assets.py` lifts the GOOD NEWS! letters, the arrow and the mascot off
  the poster. The letters are rebuilt as clean shapes and re-rendered as flat
  colour: red face, cream outline and halftone shadow, all fitted to the poster.
  That keeps them sharp, and each letter can move on its own. Where the arrow or
  the mascot covered a letter, the hidden edge is filled in.
- `countdown.html` is the scene. Every frame is a pure function of time. Open it
  through any local web server with `?t=12.5` to see a frame, or `?play` to
  watch it run. The text (school name, "ACT TWO BEGINS IN", the count length) is
  at the top of the script and in `buildRight()`.
- `render.js` screenshots each frame in headless Chromium and pipes the frames
  to ffmpeg.

Fonts are from Google Fonts under the SIL Open Font License: Roboto Slab, Jost,
Black Ops One and Noto Music.
