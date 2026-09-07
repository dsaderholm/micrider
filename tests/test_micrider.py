"""Tests for the parts that do not need Resolve, MIDI or audio.

The transport pass cannot be tested without a running Resolve, but everything
that decides *what* to write can be, and that is where the mistakes hide.
"""
import unittest

import numpy as np

from micrider.config import Config, Shape
from micrider.mcu import db_to_pitch, MIN_PITCH, CALIBRATION, BANK_SIZE
from micrider.shape import fader_path, open_fraction, GRID
from micrider.passes import banks_for
from micrider.resolve import source_offset


class TestFaderTaper(unittest.TestCase):
    def test_calibration_points_round_trip(self):
        for pitch, db in CALIBRATION:
            if db <= -140:
                continue
            self.assertEqual(db_to_pitch(db), pitch, f"{db} dB")

    def test_unity_is_the_measured_value(self):
        self.assertEqual(db_to_pitch(0.0), 3744)

    def test_silence_pins_fully_closed(self):
        self.assertEqual(db_to_pitch(float("-inf")), MIN_PITCH)
        self.assertEqual(db_to_pitch(-300.0), MIN_PITCH)

    def test_monotonic(self):
        dbs = np.arange(-60.0, 9.9, 0.1)
        p = [db_to_pitch(d) for d in dbs]
        self.assertTrue(all(b >= a for a, b in zip(p, p[1:])))


class TestFaderPath(unittest.TestCase):
    def setUp(self):
        self.shape = Shape()          # pre 0.35, hold 0.55, no ramps

    def test_only_two_levels_exist(self):
        p = fader_path([(10.0, 12.0), (40.0, 41.0)], 60.0, self.shape)
        self.assertEqual(sorted(set(int(v) for v in p)),
                         [MIN_PITCH, db_to_pitch(self.shape.open_db)])

    def test_four_keyframes_per_region(self):
        """Two edges per region; Resolve records about two points at each."""
        p = fader_path([(10.0, 12.0), (40.0, 41.0)], 60.0, self.shape)
        self.assertEqual(int((np.diff(p) != 0).sum()), 4)

    def test_edges_land_where_the_shape_says(self):
        p = fader_path([(10.0, 12.0)], 60.0, self.shape)
        edges = np.flatnonzero(np.diff(p) != 0) + 1
        self.assertAlmostEqual(edges[0] * GRID, 10.0 - self.shape.pre, places=2)
        self.assertAlmostEqual(edges[1] * GRID, 12.0 + self.shape.hold, places=2)

    def test_close_regions_merge_into_one_open_span(self):
        """A gap shorter than hold+pre must not produce a dip."""
        p = fader_path([(10.0, 12.0), (12.5, 14.0)], 60.0, self.shape)
        self.assertEqual(int((np.diff(p) != 0).sum()), 2)

    def test_distant_regions_do_not_merge(self):
        p = fader_path([(10.0, 12.0), (20.0, 22.0)], 60.0, self.shape)
        self.assertEqual(int((np.diff(p) != 0).sum()), 4)

    def test_no_regions_is_silent_throughout(self):
        p = fader_path([], 60.0, self.shape)
        self.assertTrue((p == MIN_PITCH).all())
        self.assertEqual(open_fraction(p), 0.0)

    def test_region_at_time_zero_does_not_wrap(self):
        p = fader_path([(0.1, 1.0)], 60.0, self.shape)
        self.assertEqual(int(p[0]), db_to_pitch(self.shape.open_db))


class TestBanks(unittest.TestCase):
    def test_sixteen_tracks_make_two_banks(self):
        b = banks_for(range(1, 17))
        self.assertEqual(sorted(b), [0, 1])
        self.assertEqual(b[0][0], 1)          # fader 1 of bank 0 is track A1
        self.assertEqual(b[1][0], 9)          # fader 1 of bank 1 is track A9
        self.assertEqual(b[1][7], 16)

    def test_bank_size_is_respected(self):
        for chans in banks_for(range(1, 40)).values():
            self.assertLessEqual(len(chans), BANK_SIZE)

    def test_sparse_tracks_keep_their_fader_positions(self):
        b = banks_for([3, 11])
        self.assertEqual(b[0], {2: 3})
        self.assertEqual(b[1], {2: 11})


class _Item:
    def __init__(self, left, start): self._left, self._start = left, start
    def GetLeftOffset(self): return self._left
    def GetStart(self): return self._start


class _Timeline:
    """Just enough of a Resolve timeline for the offset arithmetic."""
    def __init__(self, start, items): self._start, self._items = start, items
    def GetStartFrame(self): return self._start
    def GetItemListInTrack(self, kind, n): return self._items.get(n, [])


class TestSourceOffset(unittest.TestCase):
    FPS = 30000 / 1001.0

    def test_clip_flush_with_timeline_start(self):
        """The Good News case: 51492 source frames in, clip at timeline start."""
        tl = _Timeline(107892, {1: [_Item(51492, 107892)], 2: [_Item(51492, 107892)]})
        self.assertAlmostEqual(source_offset(tl, [1, 2], self.FPS), 1718.12, places=2)

    def test_clip_starting_later_than_the_timeline(self):
        """A clip that starts 900 frames in reaches its source 900 frames sooner."""
        tl = _Timeline(0, {1: [_Item(1800, 900)]})
        self.assertAlmostEqual(source_offset(tl, [1], self.FPS), 900 / self.FPS, places=3)

    def test_disagreeing_tracks_raise(self):
        tl = _Timeline(0, {1: [_Item(1000, 0)], 2: [_Item(9999, 0)]})
        with self.assertRaises(RuntimeError) as e:
            source_offset(tl, [1, 2], self.FPS)
        self.assertIn("do not share one source offset", str(e.exception))

    def test_empty_tracks_raise(self):
        with self.assertRaises(RuntimeError):
            source_offset(_Timeline(0, {}), [1, 2], self.FPS)

    def test_tracks_without_clips_are_skipped(self):
        tl = _Timeline(0, {1: [], 2: [_Item(300, 0)]})
        self.assertAlmostEqual(source_offset(tl, [1, 2], self.FPS), 300 / self.FPS, places=3)


class TestConfig(unittest.TestCase):
    def test_example_config_loads(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = Config.load(os.path.join(here, "examples", "good-news.toml"))
        self.assertEqual(len(cfg.tracks), 16)
        self.assertEqual(cfg.tracks[1], "Tom.wav")
        self.assertEqual(cfg.tracks[16], "Cord.wav")
        self.assertEqual(cfg.gain("Tom.wav"), -1.0)
        self.assertEqual(cfg.gain("nothing.wav"), 0.0)
        self.assertEqual(cfg.window, (0.0, 4385.0))

    def test_shape_has_no_fade_settings(self):
        """Fades were removed deliberately; they cost keyframes."""
        for gone in ("ramp_in", "ramp_out", "step"):
            self.assertFalse(hasattr(Shape(), gone), gone)


if __name__ == "__main__":
    unittest.main()
