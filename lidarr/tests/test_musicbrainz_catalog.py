import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("musicbrainz_catalog", ROOT / "python" / "MusicBrainzCatalog.py")
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


class MusicBrainzCatalogTests(unittest.TestCase):
    def test_apple_url_reads_lidarr_relationship(self):
        artist = {"links": [{"name": "homepage", "url": "https://example.com"},
                            {"name": "apple music", "url": "https://music.apple.com/us/artist/test/123"}]}
        self.assertEqual("https://music.apple.com/us/artist/test/123", catalog.apple_url(artist))

    def test_helper_result_ignores_leading_output(self):
        expected = {"artist": "Test", "release_seeds": 3}
        self.assertEqual(expected, catalog.helper_result("Preparing report\n" + json.dumps(expected) + "\n"))

    def test_positive_int_rejects_zero_and_invalid_values(self):
        self.assertEqual(5, catalog.positive_int("0", 5))
        self.assertEqual(5, catalog.positive_int("bad", 5))
        self.assertEqual(7, catalog.positive_int("7", 5))

    def test_scan_uses_existing_helper_and_saves_review(self):
        artist = {"monitored": True, "artistName": "Test Artist",
                  "foreignArtistId": "11111111-2222-3333-4444-555555555555",
                  "links": [{"url": "https://music.apple.com/us/artist/test/123"}]}
        summary = {"apple_releases": 4, "exact_musicbrainz_releases": 3, "release_seeds": 1}
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            with mock.patch.object(catalog, "STATE_DIR", state_dir), \
                 mock.patch.object(catalog, "STATE_FILE", state_dir / "state.json"), \
                 mock.patch.object(catalog, "read_config", return_value={"enableMusicBrainzCatalog": "true"}), \
                 mock.patch.object(catalog, "lidarr_artists", return_value=[artist]), \
                 mock.patch.object(catalog.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=json.dumps(summary), stderr="")) as run:
                self.assertEqual(0, catalog.scan())
            saved = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
            record = saved["artists"][artist["foreignArtistId"]]
            self.assertEqual("review", record["status"])
            self.assertEqual(1, record["release_seeds"])
            self.assertIn("--artist-mbid", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
