"""Tests for config generation, using a stubbed timeline and real temp files."""
import os
import tempfile
import tomllib
import unittest

from micrider.init import match_tracks, render


class _Item:
    def __init__(self, name): self._name = name
    def GetName(self): return self._name


class _Timeline:
    def __init__(self, tracks):
        self._t = tracks          # {number: (track name, clip name or None)}
    def GetTrackCount(self, kind): return max(self._t) if self._t else 0
    def GetTrackName(self, kind, n): return self._t.get(n, ("", None))[0]
    def GetItemListInTrack(self, kind, n):
        clip = self._t.get(n, ("", None))[1]
        return [_Item(clip)] if clip else []


class TestMatchTracks(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for f in ("Tom.wav", "Connie.wav", "Tracks.wav", "Audience.wav", "Stage.wav"):
            open(os.path.join(self.dir, f), "wb").close()

    def test_splits_mics_from_program_and_room(self):
        tl = _Timeline({1: ("Tom", "Tom.wav"), 2: ("Connie", "Connie.wav"),
                        3: ("Tracks", "Tracks.wav"),
                        4: ("Booth Condensers", "Audience.wav"),
                        5: ("Stage Condensers", "Stage.wav")})
        rows, program, room = match_tracks(tl, self.dir)
        self.assertEqual([r[2] for r in rows], ["Tom.wav", "Connie.wav"])
        self.assertEqual(program, "Tracks.wav")
        self.assertEqual([r[2] for r in room], ["Audience.wav", "Stage.wav"])

    def test_a_track_whose_file_is_missing_is_skipped(self):
        tl = _Timeline({1: ("Tom", "Tom.wav"), 2: ("Ghost", "NotHere.wav")})
        skipped = []
        rows, _, _ = match_tracks(tl, self.dir, log=skipped.append)
        self.assertEqual([r[2] for r in rows], ["Tom.wav"])
        self.assertIn("NotHere.wav", skipped[0])

    def test_empty_tracks_are_ignored(self):
        tl = _Timeline({1: ("Tom", "Tom.wav"), 2: ("Empty", None)})
        rows, _, _ = match_tracks(tl, self.dir)
        self.assertEqual(len(rows), 1)


class TestRender(unittest.TestCase):
    ROWS = [(1, "Tom", "Tom.wav"), (2, "Connie", "Connie.wav")]

    def _load(self, text):
        return tomllib.loads(text)

    def test_output_is_valid_toml_with_the_expected_shape(self):
        d = self._load(render(self.ROWS, "Tracks.wav", "E:/Show/Audio", 1200.0,
                              {"Tom.wav": -1.0, "Connie.wav": 7.4}))
        self.assertEqual(d["audio"]["dir"], "E:/Show/Audio")
        self.assertEqual(d["audio"]["program"], "Tracks.wav")
        self.assertEqual(d["audio"]["offset"], "auto")
        self.assertEqual(d["audio"]["window"], [0.0, 1200.0])
        self.assertEqual(d["timeline"]["tracks"], ["Tom.wav", "Connie.wav"])
        self.assertEqual(d["timeline"]["first_track"], 1)
        self.assertEqual(d["gains"]["Connie.wav"], 7.4)

    def test_gaps_in_track_numbers_are_addressed_explicitly(self):
        """Consecutive tracks can use a list; a gap cannot, or they would shift."""
        d = self._load(render([(1, "Tom", "Tom.wav"), (5, "Connie", "Connie.wav")],
                              None, "d", 10.0))
        self.assertNotIn("tracks", d["timeline"])
        self.assertEqual(d["timeline"]["map"], {"1": "Tom.wav", "5": "Connie.wav"})

    def test_room_mics_are_commented_out_not_dropped(self):
        text = render(self.ROWS, None, "d", 10.0, room=[(9, "Booth", "Audience.wav")])
        self.assertIn("Audience.wav", text)
        self.assertNotIn("Audience.wav", self._load(text)["timeline"]["tracks"])

    def test_no_program_track_leaves_a_hint_not_a_value(self):
        d = self._load(render(self.ROWS, None, "d", 10.0))
        self.assertNotIn("program", d["audio"])

    def test_no_tracks_is_an_error(self):
        with self.assertRaises(ValueError):
            render([], None, "d", 10.0)


if __name__ == "__main__":
    unittest.main()
