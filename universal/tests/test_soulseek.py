import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("soulseek", Path(__file__).parents[1]/"python/Soulseek.py")
soulseek = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soulseek)


def response(names):
    return [{"username": "test-peer", "files": [{"filename": name, "size": 4} for name in names]}]


class MatchingTests(unittest.TestCase):
    def test_movie_requires_exact_title_year_and_rejects_samples(self):
        target = dict(app="radarr", id=1, title="Movie", year=2020)
        self.assertIsNotNone(soulseek.choose_files(target, response(["Movie.2020.1080p.mkv"])))
        for name in ("Movie.2021.1080p.mkv", "Movie.2.2020.1080p.mkv", "Movie.2020.Sample.mkv", "Movie.2020.mkv.exe", "..\\Movie.2020.mkv"):
            self.assertIsNone(soulseek.choose_files(target, response([name])))

    def test_episode_requires_correct_single_episode(self):
        target = dict(app="sonarr", id=1, title="Series", season=1, episode=2)
        self.assertIsNotNone(soulseek.choose_files(target, response(["Series.S01E02.1080p.mkv"])))
        for name in ("Series.S01E03.mkv", "Series.S02E02.mkv", "Series.S01E01E02.mkv", "Series.S01E02E03.mkv", "Series.Season.1.mkv", "Other.Series.S01E02.mkv"):
            self.assertIsNone(soulseek.choose_files(target, response([name])))

    def test_album_requires_all_distinct_tracks_from_same_peer(self):
        target = dict(app="lidarr", title="Album", artist="Artist", tracks=[{"title": "First"}, {"title": "Second"}])
        files = ["Artist\\Album\\01 First.flac", "Artist\\Album\\02 Second.flac"]
        self.assertIsNotNone(soulseek.choose_files(target, response(files)))
        self.assertIsNone(soulseek.choose_files(target, response(files[:1])))
        self.assertIsNone(soulseek.choose_files(target, response(files[:1])+[{"username": "other-peer", "files": [{"filename": files[1], "size": 4}]}]))

    def test_ambiguous_duplicate_track_is_rejected(self):
        target = dict(app="lidarr", title="Album", artist="Artist", tracks=[{"title": "Song"}])
        self.assertIsNone(soulseek.choose_files(target, response(["Artist\\Album\\01 Song.flac", "Artist\\Album\\02 Song.flac"])))

    def test_numeric_song_titles_are_preserved_after_track_number(self):
        target = dict(app="lidarr", title="Album", artist="Artist", tracks=[{"title": "99 Problems"}])
        self.assertIsNotNone(soulseek.choose_files(target, response(["Artist\\Album\\01 99 Problems.flac"])))

    def test_import_rejections_and_wrong_identity_never_forced(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary)/"Movie.2020.mkv"
            file.write_bytes(b"test")
            target = dict(app="radarr", id=1)
            decision = {"path": str(file), "movie": {"id": 1}, "quality": {"quality": {"id": 7}}, "rejections": []}
            payload = soulseek.import_payload("radarr", target, [decision], temporary)
            self.assertEqual(payload["importMode"], "copy")
            self.assertEqual(payload["files"][0]["movieId"], 1)
            decision["rejections"] = [{"reason": "wrong quality"}]
            self.assertIsNone(soulseek.import_payload("radarr", target, [decision], temporary))
            decision.update(rejections=[], movie={"id": 2})
            self.assertIsNone(soulseek.import_payload("radarr", target, [decision], temporary))

    def test_episode_identity_and_complete_music_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary)/"Song.flac"
            file.write_bytes(b"test")
            quality = {"quality": {"id": 1}}
            target = dict(id=1, artistId=2, missingTrackIds=[3, 4])
            decision = dict(path=str(file), quality=quality, rejections=[], artist={"id": 2}, album={"id": 1}, albumReleaseId=9, tracks=[{"id": 3}])
            self.assertIsNone(soulseek.import_payload("lidarr", target, [decision], temporary))
            target["missingTrackIds"] = [3]
            self.assertEqual(soulseek.import_payload("lidarr", target, [decision], temporary)["files"][0]["trackIds"], [3])
            episode = dict(path=str(file), quality=quality, rejections=[], series={"id": 2}, episodes=[{"id": 3}])
            self.assertIsNone(soulseek.import_payload("sonarr", dict(id=4, seriesId=2), [episode], temporary))

    def test_external_import_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root/"batch"
            folder.mkdir()
            file = root/"outside.mkv"
            file.write_bytes(b"test")
            decision = dict(path=str(file), movie={"id": 1}, quality={"quality": {"id": 7}})
            self.assertIsNone(soulseek.import_payload("radarr", dict(id=1), [decision], folder))


class Arr:
    def __init__(self):
        self.movies = [{"id": 1, "title": "Movie", "year": 2020, "monitored": True, "hasFile": False, "digitalRelease": "2020-01-01T00:00:00Z"}]
        self.queue = []
        self.commands = []
        self.decisions = []

    def call(self, path, data=None, **query):
        if path.endswith("movie"):
            return self.movies
        if path.endswith("queue"):
            return {"records": self.queue, "totalRecords": len(self.queue)}
        if path.endswith("manualimport"):
            return self.decisions
        if path.endswith("command"):
            self.commands.append(data)
            return {"id": 123}
        if path.endswith("command/123"):
            return {"status": "completed"}
        raise AssertionError(path)


class Soulseek:
    client = "slskd"
    def __init__(self):
        self.version = "0.26.0"
        self.connected = True
        self.searches = {}
        self.batches = {}
        self.posts = []
        self.lose_reply = False
        self.results = response(["Movie.2020.1080p.mkv"])

    def call(self, path, data=None, **query):
        if path.endswith("application/version"):
            return self.version
        if path.endswith("application"):
            return {"server": {"isConnected": self.connected}, "capabilities": ["search", "download-batches", "batch-destination"]}
        if path == "api/v0/searches":
            self.posts.append((path, data))
            self.searches[data["id"]] = {"isComplete": True, "responses": self.results}
            return {}
        if path.startswith("api/v0/searches/"):
            return self.searches[path.rsplit("/", 1)[-1]]
        if path == "api/v0/transfers/downloads/batches":
            self.posts.append((path, data))
            self.batches[data["id"]] = {"transfers": [{**f, "state": "Queued"} for f in data["files"]]}
            if self.lose_reply:
                raise soulseek.ApiError()
            return {}
        if path.startswith("api/v0/transfers/downloads/batches/"):
            ident = path.rsplit("/", 1)[-1]
            if ident not in self.batches:
                raise soulseek.ApiError(404)
            return self.batches[ident]
        raise AssertionError(path)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.arr = Arr()
        self.slsk = Soulseek()
        self.now = 1700000000
        self.worker = soulseek.Worker("radarr", self.arr, self.slsk, self.root, self.root, delay=0, clock=lambda: self.now)

    def tearDown(self):
        self.worker.db.close()
        self.temp.cleanup()

    def tick(self, seconds=301):
        self.now += seconds
        self.worker.tick()

    def row(self):
        return self.worker.db.execute("SELECT * FROM jobs WHERE id=1").fetchone()

    def reach_batch(self):
        for _ in range(3):
            self.tick()
        return next(iter(self.slsk.batches))

    def test_version_and_disconnection_prevent_searches(self):
        self.slsk.version = "0.25.1"
        self.tick()
        self.assertFalse(self.slsk.posts)
        self.slsk.version = "0.26.0"
        self.slsk.connected = False
        self.tick()
        self.assertFalse(self.slsk.posts)

    def test_nicotine_uses_plugin_version_and_capabilities(self):
        self.slsk.client, self.slsk.version = "nicotine", "0.1.0"
        self.tick()
        self.assertEqual(self.row()["state"], "searching")

    def test_delay_and_queue_suppress_duplicates(self):
        self.worker.delay = 3600
        self.tick()
        self.assertFalse(self.slsk.posts)
        self.arr.queue = [{"movieId": 1}]
        self.tick(3601)
        self.assertFalse(self.slsk.posts)
        self.arr.queue = []
        self.tick()
        self.assertEqual(self.row()["state"], "searching")

    def test_unmonitored_existing_and_future_items_skipped(self):
        for changes in ({"monitored": False}, {"hasFile": True}, {"digitalRelease": "2099-01-01"}):
            original = self.arr.movies[0].copy()
            self.arr.movies[0].update(changes)
            self.tick()
            self.assertFalse(self.slsk.posts)
            self.arr.movies[0] = original

    def test_accepted_batch_with_lost_reply_is_not_repeated(self):
        self.slsk.lose_reply = True
        ident = self.reach_batch()
        self.assertEqual(self.row()["state"], "enqueueing")
        self.worker.db.close()
        self.worker = soulseek.Worker("radarr", self.arr, self.slsk, self.root, self.root, delay=0, clock=lambda: self.now)
        self.tick()
        self.assertEqual(self.row()["state"], "downloading")
        self.assertEqual(len([p for p, _ in self.slsk.posts if p.endswith("batches")]), 1)
        self.assertIn(ident, self.slsk.batches)

    def test_missing_search_waits_and_does_not_requeue_each_tick(self):
        self.slsk.results = []
        self.tick()
        self.tick()
        self.assertEqual(self.row()["state"], "waiting")
        self.tick()
        self.assertEqual(len(self.slsk.posts), 1)

    def test_partial_batch_requires_review(self):
        ident = self.reach_batch()
        self.slsk.batches[ident]["transfers"] = []
        self.tick()
        self.assertEqual(self.row()["state"], "review")
        self.assertFalse(self.arr.commands)

    def test_complete_movie_import_is_validated_and_confirmed_by_library(self):
        ident = self.reach_batch()
        self.slsk.batches[ident]["transfers"][0]["state"] = "Completed, Succeeded"
        self.tick()
        folder = self.root/"arr-scripts/radarr"/ident
        folder.mkdir(parents=True)
        file = folder/"Movie.2020.1080p.mkv"
        file.write_bytes(b"test")
        self.arr.decisions = [{"path": str(file), "movie": {"id": 1}, "quality": {"quality": {"id": 7}}, "rejections": []}]
        self.tick()
        self.assertEqual(len(self.arr.commands), 1)
        self.assertEqual(self.arr.commands[0]["files"][0]["movieId"], 1)
        self.arr.movies[0]["hasFile"] = True
        self.tick()
        self.assertEqual(self.row()["state"], "done")

    def test_completed_transfer_without_file_never_imports(self):
        ident = self.reach_batch()
        self.slsk.batches[ident]["transfers"][0]["state"] = "Completed, Succeeded"
        self.tick()
        self.tick()
        self.assertEqual(self.row()["state"], "downloaded")
        self.assertFalse(self.arr.commands)

    def test_streaming_fallback_requires_completed_miss_marker(self):
        target = {"id": 1}
        row = {"first_seen": self.now}
        self.worker.delay = 3600
        self.worker.app, self.worker.audio, self.worker.sources = "lidarr", True, "all"
        self.assertFalse(self.worker.ready(row, target))
        marker = self.root/"extended/soulseek/fallback/1"
        marker.parent.mkdir(parents=True)
        marker.touch()
        self.assertTrue(self.worker.ready(row, target))
        self.worker.sources = "soulseek"
        self.assertTrue(self.worker.ready({"first_seen": self.now}, target))

    def test_failed_streaming_worker_allows_delayed_fallback(self):
        self.worker.app, self.worker.audio, self.worker.sources = "lidarr", True, "all"
        self.assertTrue(self.worker.ready({"first_seen": self.now-7200}, {"id": 1}))

    def test_api_failure_does_not_assume_empty_download_queue(self):
        original = self.arr.call
        def broken(path, *args, **kwargs):
            if path.endswith("queue"):
                raise soulseek.ApiError(503)
            return original(path, *args, **kwargs)
        self.arr.call = broken
        with self.assertRaises(soulseek.ApiError):
            self.tick()
        self.assertFalse(self.slsk.posts)

    def test_active_job_limit(self):
        self.arr.movies = [{**self.arr.movies[0], "id": number} for number in range(1, 10)]
        self.tick()
        self.tick()
        self.assertEqual(len([p for p, _ in self.slsk.posts if p.endswith("searches")]), 3)


class ClientTests(unittest.TestCase):
    def test_nicotine_adapter_routes_to_own_api_version(self):
        client = soulseek.SoulseekApi("http://example.invalid:12339", "test-key", "nicotine")
        with patch.object(soulseek.Api, "call", return_value={}) as call:
            client.call("api/v0/searches", {"searchText": "Song"})
        call.assert_called_once_with("api/v1/searches", {"searchText": "Song"})


if __name__ == "__main__":
    unittest.main()
