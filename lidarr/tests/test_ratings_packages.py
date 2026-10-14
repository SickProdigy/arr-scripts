"""Integration acceptance tests against the installed pinned packages, with fake APIs."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse, parse_qs

try:
    from sonicratings import automation, cli
    from musicbrainz_helper import ratings_worker as helper
    PACKAGES_AVAILABLE = hasattr(helper, "apply_generated")
except ImportError:
    PACKAGES_AVAILABLE = False

RECORDING = "00000000-0000-4000-8000-000000000001"
TRACK = {"path": "/music/Artist/Album/Song.flac", "recording": RECORDING,
         "artist": "Artist", "title": "Song", "album": "Album"}
SONG = {"id": "song", "path": "Artist/Album/Song.flac", "musicBrainzId": RECORDING,
        "artist": "Artist", "title": "Song", "album": "Album", "userRating": 0}


@unittest.skipUnless(PACKAGES_AVAILABLE, "Install the pinned SonicRatings and musicbrainz-helper packages first")
class RatingsPackageTests(unittest.TestCase):
    def test_generation_and_contribution_preserve_direction_and_source(self):
        for provider in ("musicbrainz", "discogs"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                song = copy.deepcopy(SONG)
                server = Mock()
                server.songs.side_effect = lambda: {"song": copy.deepcopy(song)}
                server.get_song.side_effect = lambda _: copy.deepcopy(song)
                mb = Mock()
                mb.submit.return_value = None
                provider_client = Mock()
                provider_client.find_track.return_value = {"average": 4.2, "rating": 4,
                    "matched_title": "Song", "release_id": 10, "selection": "exact", "votes": 20}
                def request(url, **kwargs):
                    method = urlparse(url).path.rsplit("/", 1)[-1]
                    if method == "getSong":
                        data = {"subsonic-response": {"status": "ok", "song": copy.deepcopy(song)}}
                    elif method == "setRating":
                        pending = json.loads((root / "sonic/generated-ratings.json").read_text())["song"]
                        self.assertTrue(pending["pending"])
                        song["userRating"] = int(parse_qs(urlparse(url).query)["rating"][0])
                        data = {"subsonic-response": {"status": "ok"}}
                    elif method == "recording":
                        data = {"recordings": [{"id": RECORDING, "title": "Song"}]}
                    elif method == RECORDING:
                        data = {"rating": {"value": 4}}
                    else:
                        raise AssertionError("Unexpected request")
                    response = Mock(status_code=200)
                    response.json.return_value = data
                    return response
                flags = ["--provider", provider, "--unrated-only", "--log-dir", str(root / "sonic")]
                if provider == "discogs": flags.append("--discogs-inherit-album-rating")
                options = cli.parse_args(flags)
                options.provenance_file = root / "sonic/generated-ratings.json"
                automation.write_json(root / "sonic/jobs/import.json", {"tracks": [TRACK]})
                helper.genres.write_json(root / "helper/jobs/import.json", {"tracks": [TRACK]})
                with patch.dict(os.environ, {"NAV_BASE_URL": "https://nav.example.test", "NAV_USER": "test", "NAV_PASS": "secret"}), patch.object(cli.requests, "get", side_effect=request), patch.object(cli, "DiscogsClient", return_value=provider_client), patch.object(cli.time, "sleep"):
                    self.assertEqual(automation.work(root / "sonic", server, options), 0)
                self.assertEqual(song["userRating"], 4)
                provenance = json.loads(options.provenance_file.read_text())
                self.assertEqual(provenance["song"]["source"], provider)
                self.assertFalse(provenance["song"]["pending"])
                self.assertEqual(helper.run_worker(root / "helper", server, mb, submit=True, account="test", now=1000, generated_ratings=provenance), 0)
                self.assertEqual(mb.submit.call_count, 0 if provider == "musicbrainz" else 1)
                # A later personal change is eligible even when generation came from MusicBrainz.
                song["userRating"] = 3
                self.assertEqual(helper.run_worker(root / "helper", server, mb, submit=True, account="test", now=2000, generated_ratings=provenance), 0)
                self.assertEqual(mb.submit.call_count, 1 if provider == "musicbrainz" else 2)
                self.assertEqual(song["userRating"], 3)


if __name__ == "__main__":
    unittest.main()
