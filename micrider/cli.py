"""micrider command line."""
from __future__ import annotations
import argparse, json, os, sys

from .config import Config
from .analyze import Show
from .shape import fader_path, open_fraction
from .mcu import Surface, BANK_SIZE
from . import resolve as rv
from .passes import banks_for, write_bank, Stalled

CHECKLIST = """
Before any pass, in Resolve's Fairlight page:
  1. Preferences > Control Panels > MIDI: protocol "MCU compatible";
     MIDI Input = the port micrider sends to, MIDI Output = a second, separate port.
  2. The automation toolbar's Touch group must be LATCH, not Off.
     On Off, Resolve accepts fader messages and silently ignores them - this is
     the single most common reason a pass appears to do nothing.  It resets to
     Off every time Resolve restarts.
  3. Mode Write (or Latch), Enables > Fader lit, On Stop > Hold.
  4. Every track you are writing must be in Latch, Write or Global automation mode.
"""


def _cache(cfg, args): return args.cache or os.path.join(cfg.audio_dir, ".micrider.npz")


def _show(cfg, args, verbose=True) -> Show:
    s, c = Show(cfg), _cache(cfg, args)
    if os.path.exists(c) and not args.refresh:
        return s.load(c)
    if verbose: print("analysing audio (this happens once)...", flush=True)
    return s.build(c, verbose)


def _plan(cfg, show, duration):
    """{track number: (regions, fader path)} for every configured track."""
    plan = {}
    for row, tn in enumerate(show.numbers):
        r = show.regions(row)
        plan[tn] = (r, fader_path(r, duration, cfg.shape))
    return plan


def cmd_doctor(cfg, args):
    import mido
    print("MIDI outputs:", *[f"\n   {n}" for n in mido.get_output_names()] or " none")
    print("MIDI inputs: ", *[f"\n   {n}" for n in mido.get_input_names()] or " none")
    ok = cfg.midi_out in mido.get_output_names()
    print(f"\nconfigured output {cfg.midi_out!r}: {'found' if ok else 'NOT FOUND'}")
    try:
        tl = rv.timeline(); clock = rv.Clock(tl)
        print(f"Resolve: timeline {tl.GetName()!r}, {clock.duration() / 60:.1f} min, "
              f"{tl.GetTrackCount('audio')} audio tracks, playhead {tl.GetCurrentTimecode()}")
        for tn in sorted(cfg.tracks):
            print(f"   A{tn:<3} {tl.GetTrackName('audio', tn):<18} <- {cfg.tracks[tn]}")
    except Exception as e:
        print("Resolve: NOT reachable -", e)
    missing = [f for f in cfg.tracks.values() if not os.path.exists(cfg.path(f))]
    print("missing audio files:", ", ".join(missing) if missing else "none")
    print(CHECKLIST)


def cmd_gains(cfg, args):
    g = rv.read_clip_gains(rv.timeline(), args.workdir or cfg.audio_dir)
    for k in sorted(g): print(f"  {k:<20} {g[k]:+7.2f} dB")
    if args.out:
        json.dump(g, open(args.out, "w"), indent=1)
        print(f"\nwrote {args.out}")


def cmd_plan(cfg, args):
    show = _show(cfg, args)
    dur = cfg.window[1] or rv.Clock(rv.timeline()).duration()
    plan = _plan(cfg, show, dur)
    print(f"\n{'track':<6}{'name':<16}{'regions':>9}{'open':>8}   bank/fader")
    for tn, (r, path) in plan.items():
        b, ch = (tn - 1) // BANK_SIZE, (tn - 1) % BANK_SIZE
        print(f"A{tn:<5}{cfg.tracks[tn]:<16}{len(r):>9}{open_fraction(path) * 100:7.1f}%"
              f"   bank {b} fader {ch + 1}")
        if args.verbose:
            for a, bb in r: print(f"        {a:8.2f} - {bb:8.2f}")
    total = sum(len(r) for r, _ in plan.values())
    print(f"\n{total} regions; {len(banks_for(plan))} pass(es) of "
          f"{(cfg.window[1] - cfg.window[0]) / 60:.1f} min each")


PARTIAL_WARNING = """
REFUSING a partial-range pass.

You asked to write {t0:.0f}-{t1:.0f}s, but the configured window is {f0:.0f}-{f1:.0f}s.

In Write mode, stopping the transport does not just stop writing.  Resolve
propagates the held value forward from the punch-out point, which ERASES every
automation point after it, to the end of the timeline.  A short pass in the
middle of a finished act will destroy the rest of that act on these tracks.

Either run the full window (drop --start/--end), or set the tracks to Latch
first - Latch punches out cleanly and leaves later automation alone - and then
pass --partial to say you have done that.
"""


def cmd_write(cfg, args):
    show = _show(cfg, args)
    tl = rv.timeline(); clock = rv.Clock(tl)
    full0 = cfg.window[0]
    full1 = cfg.window[1] or clock.duration()
    t0 = args.start if args.start is not None else full0
    t1 = args.end if args.end is not None else full1
    if (t0 > full0 + 0.01 or t1 < full1 - 0.01) and not args.partial:
        print(PARTIAL_WARNING.format(t0=t0, t1=t1, f0=full0, f1=full1), file=sys.stderr)
        return 2
    plan = _plan(cfg, show, t1)
    groups = banks_for(plan)
    wanted = groups if args.bank is None else {args.bank: groups[args.bank]}
    print(CHECKLIST if not args.yes else "", end="")
    if not args.yes:
        if input("Everything above is set? [y/N] ").strip().lower() != "y":
            return 1
    for bank, chans in sorted(wanted.items()):
        names = ", ".join(cfg.tracks[t] for t in chans.values())
        print(f"\n=== bank {bank}: {names} ===", flush=True)
        paths = {ch: plan[tn][1] for ch, tn in chans.items()}
        with Surface(cfg.midi_out, cfg.midi_in) as s:
            try:
                n = write_bank(clock, s, paths, bank, t0, t1, cfg.shape)
                print(f"bank {bank} done, {n} fader moves", flush=True)
            except Stalled as e:
                print(f"bank {bank} FAILED: {e}", file=sys.stderr); return 2
    if args.save:
        pm = rv.connect().GetProjectManager()
        print("saved:", pm.SaveProject())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser("micrider", description=__doc__)
    p.add_argument("-c", "--config", default="micrider.toml")
    p.add_argument("--cache"); p.add_argument("--refresh", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check MIDI, Resolve and the manual settings")
    g = sub.add_parser("gains", help="read clip gain out of the current timeline")
    g.add_argument("-o", "--out"); g.add_argument("--workdir")
    pl = sub.add_parser("plan", help="show what would be written, without writing")
    pl.add_argument("-v", "--verbose", action="store_true")
    w = sub.add_parser("write", help="write automation in real time")
    w.add_argument("--bank", type=int); w.add_argument("--start", type=float)
    w.add_argument("--end", type=float); w.add_argument("--save", action="store_true")
    w.add_argument("--partial", action="store_true",
                   help="allow a partial range; only safe with tracks set to Latch")
    w.add_argument("-y", "--yes", action="store_true")
    args = p.parse_args(argv)
    cfg = Config.load(args.config)
    return {"doctor": cmd_doctor, "gains": cmd_gains,
            "plan": cmd_plan, "write": cmd_write}[args.cmd](cfg, args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
