import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

sys.path.insert(0, str(Path(__file__).parents[1]/"python"))
from SoulseekAudio import AudioPolicy, AudioQualityError


class AudioTests(unittest.TestCase):
    def test_lossless_preference_and_mp3_fallback(self):
        policy = AudioPolicy()
        flac = dict(filename="Song.flac", bitDepth=16, sampleRate=44100)
        mp3 = dict(filename="Song.mp3", bitRate=320)
        self.assertLess(policy.rank(flac), policy.rank(mp3))
        self.assertIsNotNone(policy.rank(mp3))
        self.assertIsNone(AudioPolicy("native", "lossless", True).rank(mp3))

    def test_master_requires_high_resolution_when_strict(self):
        policy = AudioPolicy(strict=True)
        self.assertIsNone(policy.rank(dict(filename="Song.flac", bitDepth=16, sampleRate=44100)))
        self.assertIsNotNone(policy.rank(dict(filename="Song.flac", bitDepth=24, sampleRate=96000)))

    def test_m4a_codec_overrides_peer_hint(self):
        policy = AudioPolicy("native", "lossless", True)
        self.assertIsNone(policy.rank(dict(filename="Song.m4a", codec="aac", bitDepth=24, bitRate=900)))
        self.assertIsNotNone(policy.rank(dict(filename="Song.m4a", codec="alac")))

    def test_native_fallback_needs_no_transcoding(self):
        files = [dict(filename="Song.mp3", size=4)]
        with patch.object(AudioPolicy, "probe", side_effect=AssertionError("Unexpected probe")):
            self.assertEqual(AudioPolicy().prepare(Path("unused"), files)[0]["size"], 4)

    def test_lossy_fallback_is_preserved_without_upconversion(self):
        policy = AudioPolicy("alac", "320")
        with patch.object(policy, "probe", return_value=dict(filename="Song.mp3", codec="mp3", bitRate=128)):
            self.assertEqual(policy.prepare(Path("unused"), [dict(filename="Song.mp3", size=4)])[0]["filename"], "Song.mp3")

    def test_conversion_failure_keeps_source_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder/"Song.flac"
            source.write_bytes(b"original")
            policy = AudioPolicy("mp3", "320")
            with patch.object(policy, "probe", return_value=dict(filename=source.name, codec="flac")), patch("SoulseekAudio.subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
                with self.assertRaises(AudioQualityError):
                    policy.prepare(folder, [dict(filename=source.name, size=8)])
            self.assertEqual(source.read_bytes(), b"original")
            self.assertEqual(list(folder.iterdir()), [source])

    def test_conversion_records_new_size_and_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder/"Song.flac"
            source.write_bytes(b"original")
            policy = AudioPolicy("mp3", "320")
            def encode(args, **kwargs):
                Path(args[-1]).write_bytes(b"encoded audio")
            with patch.object(policy, "probe", side_effect=[dict(filename=source.name, codec="flac"), dict(filename="temporary.mp3", codec="mp3")]), patch("SoulseekAudio.subprocess.run", side_effect=encode):
                result = policy.prepare(folder, [dict(filename=source.name, size=8)])
            self.assertEqual(result, [dict(filename="Song.mp3", size=13, sourceFilename="Song.flac")])
            self.assertFalse(source.exists())
            self.assertEqual((folder/"Song.mp3").read_bytes(), b"encoded audio")
