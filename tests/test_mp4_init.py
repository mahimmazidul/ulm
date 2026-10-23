"""Stdlib-unittest suite (no network, no third-party deps)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestMP4Init(unittest.TestCase):
    """The ISM init-segment writer must produce verifiable boxes."""

    def _fake_track(self, codec: str, private: str = "", fourcc: str = "", channels=None):
        from ulm.core.utils.mp4_init import build_init_segment  # avoids top-level import costs

        class _Fake:
            pass

        track = _Fake()
        track.data = {"codec_private_data": private, "fourcc": fourcc}
        track.width, track.height = 1920, 1080
        track.codec = codec
        track.channels = channels
        return build_init_segment(track)

    def test_h264_init_written(self):
        data = self._fake_track("H264", "000000016742C029DA010801F37E1031640000000168CE3C80")
        self.assertTrue(data)
        self.assertIn(b"ftyp", data[:8])

    def test_every_runner_writes_valid_box_tree(self):
        for codec, private, fourcc, channels in (
            ("H264", "000000016742C029DA010801F37E1031640000000168CE3C80", "H264", None),
            ("H265", "", "HVC1", None),
            ("AAC", "", "AACL", 2),
            ("EAC3", "", "EAC3", 6),
        ):
            data = self._fake_track(codec, private, fourcc, channels)
            self.assertTrue(data, f"{codec} produced no init segment")


class TestTrackSelection(unittest.TestCase):
    """Tracks.select_video / select_audio keep best-availability semantics."""

    def _build_tracks(self):
        from ulm.core.constants import Descriptor
        from ulm.core.tracks import Audio, Tracks, Video

        tracks = Tracks()
        tracks.add(Video(height=1920, width=1080, bitrate=2500000, url="", urls=[], descriptor=Descriptor.SIMPLE))
        tracks.add(Video(height=720, width=1280, bitrate=1200000, url="", urls=[], descriptor=Descriptor.SIMPLE))
        tracks.add(Audio(channels=2, bitrate=128000, url="", urls=[], descriptor=Descriptor.SIMPLE))
        tracks.add(Audio(channels=6, bitrate=768000, url="", urls=[], descriptor=Descriptor.SIMPLE))
        return tracks

    def test_select_video_best(self):
        tracks = self._build_tracks()
        best = tracks.select_video(best=True)
        self.assertEqual(best.height, 1920)

    def test_select_video_worst(self):
        tracks = self._build_tracks()
        worst = tracks.select_video(best=False)
        self.assertEqual(worst.height, 720)

    def test_select_audio_best(self):
        tracks = self._build_tracks()
        best = tracks.select_audio(best=True)
        self.assertEqual(best.channels, 6)


class TestVault(unittest.TestCase):
    """SQLite vault roundtrips kid/key per service."""

    def test_roundtrip(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            os.environ["UNSHACKLE_DATA"] = temp
            from ulm.core.vaults import SQLiteVault, Vaults

            vault = SQLiteVault(Path(temp) / "v.db")
            kid = "1" * 32
            self.assertTrue(vault.add_key(kid, "2" * 32, "SVC"))
            self.assertFalse(vault.add_key(kid, "2" * 32, "SVC"))  # idempotent
            self.assertEqual(vault.get_key(kid, "SVC")["key"], "2" * 32)
            self.assertIsNone(vault.get_key(kid, "OTHER"))
            manager = Vaults([vault])
            self.assertEqual(manager.find_key(kid, "SVC")["vault"], "v.db")


if __name__ == "__main__":
    unittest.main()
