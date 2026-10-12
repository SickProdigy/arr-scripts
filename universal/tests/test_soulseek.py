import importlib.util
import json
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
            self.assertEqual(payload["files"][0]["releaseGroup"], "Soulseek")
            decision["releaseGroup"] = "OriginalGroup"
            self.assertEqual(soulseek.import_payload("radarr", target, [decision], temporary)["files"][0]["releaseGroup"], "OriginalGroup")
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
        if path.endswith("wanted/missing"):
            return {"records": self.movies, "totalRecords": len(self.movies)}
        if path.endswith("movie/1"):
            return self.movies[0]
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
        if path.endswith("/cancel") or path.endswith("/clear"):
            self.posts.append((path,data))
            ident = path.split("/")[-2]
            batch = self.batches[ident]
            if path.endswith("/cancel"):
                for file in batch["transfers"]:
                    if file["state"] != "Completed, Succeeded": file["state"] = "Completed, Aborted"
            else:
                batch["cleared"] = True
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

    def test_failed_candidate_skipped_and_cached_alternative_survives_restart(self):
        self.tick()
        data = json.loads(self.row()["data"])
        first = dict(username="first",files=[dict(filename="album/song.flac",size=4)])
        second = dict(username="second",files=[dict(filename="album/song.flac",size=4)])
        data.update(first,batch=str(soulseek.uuid.uuid4()),candidates=[first,second])
        self.worker.fail_candidate(1,data,"import rejected")
        self.worker.db.close()
        self.worker = soulseek.Worker("radarr",self.arr,self.slsk,self.root,self.root,delay=0,clock=lambda:self.now)
        saved = json.loads(self.row()["data"])
        self.worker.next_candidate(1,saved)
        next_data = json.loads(self.row()["data"])
        self.assertEqual(next_data["username"],"second")
        self.assertEqual(next_data["search"],data["search"])
        self.assertNotEqual(next_data["batch"],data["batch"])

    def test_exhaustion_waits_and_folder_exclusion_expires(self):
        self.tick()
        candidate = dict(username="peer",files=[dict(filename="album/song.flac",size=4)])
        data = dict(candidate,batch=str(soulseek.uuid.uuid4()),candidates=[candidate])
        self.worker.fail_candidate(1,data,"failed transfer")
        same_folder = dict(username="peer",files=[dict(filename="album/song.mp3",size=3)])
        self.assertTrue(self.worker.excluded(1,same_folder))
        self.worker.next_candidate(1,data)
        self.assertEqual(self.row()["state"],"waiting")
        self.assertEqual(self.row()["next_run"],self.now+7*86400)
        self.now += 30*86400+1
        self.assertFalse(self.worker.excluded(1,candidate))

    def test_failed_cleanup_retains_files_when_client_is_active(self):
        self.tick()
        batch = str(soulseek.uuid.uuid4())
        folder = self.root/batch
        folder.mkdir()
        source = folder/"Movie.mkv"
        source.write_bytes(b"test")
        data = dict(batch=batch,destination=batch,username="peer",files=[dict(filename="Movie.mkv",size=4)])
        self.worker.fail_candidate(1,data,"failed transfer")
        self.slsk.client = "nicotine"
        with patch.object(self.slsk,"call",side_effect=soulseek.ApiError(409)):
            with self.assertRaises(soulseek.ApiError):
                self.worker.failed_cleanup(1,data,[])
        self.assertTrue(source.exists())
        self.assertEqual(self.row()["state"],"failure_cleanup")

    def test_failed_cleanup_clears_client_before_file_removal(self):
        self.tick()
        batch = str(soulseek.uuid.uuid4())
        folder = self.root/batch
        folder.mkdir()
        source = folder/"Movie.mkv"
        source.write_bytes(b"test")
        data = dict(batch=batch,destination=batch,username="peer",files=[dict(filename="Movie.mkv",size=4)],candidates=[])
        self.slsk.client = "nicotine"
        def clear(path,payload):
            self.assertTrue(source.exists())
            self.assertTrue(path.endswith(("/cancel","/clear")))
        with patch.object(self.slsk,"call",side_effect=clear):
            self.worker.failed_cleanup(1,data,[])
        self.assertFalse(source.exists())
        self.assertEqual(self.row()["state"],"waiting")

    def test_version_and_disconnection_prevent_searches(self):
        self.slsk.version = "0.25.1"
        self.tick()
        self.assertFalse(self.slsk.posts)
        self.slsk.version = "0.26.0"
        self.slsk.connected = False
        self.tick()
        self.assertFalse(self.slsk.posts)

    def test_diagnostic_bypasses_lock_and_never_submits_searches(self):
        import os
        import io
        from contextlib import redirect_stdout
        environment = {"SOULSEEK_APP": "radarr", "SOULSEEK_ARR_URL": "http://arr", "SOULSEEK_ARR_API_KEY": "test-key",
                       "SOULSEEK_URL": "http://client", "SOULSEEK_API_KEY": "test-key", "SOULSEEK_DOWNLOAD_PATH": str(self.root)}
        output = io.StringIO()
        with patch.dict(os.environ, environment), patch('sys.argv', ['Soulseek.py', '--diagnose']), \
                patch.object(soulseek, 'Api', return_value=self.arr), patch.object(soulseek, 'SoulseekApi', return_value=self.slsk), \
                patch.object(soulseek, 'worker_lock', side_effect=AssertionError('must not acquire lock')), redirect_stdout(output):
            soulseek.main()
        self.assertIn('Arr wanted/missing total: 1', output.getvalue())
        self.assertIn('Soulseek connected: True', output.getvalue())
        self.assertFalse(self.slsk.posts)
        self.assertIsNone(self.row())

    def test_old_nicotine_api_defers_before_searching(self):
        self.slsk.client,self.slsk.version="nicotine","0.1.2"
        self.tick()
        self.assertFalse(self.slsk.searches)

    def test_nicotine_uses_plugin_version_and_capabilities(self):
        self.slsk.client, self.slsk.version = "nicotine", "0.1.3"
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
        folder = self.root/ident
        folder.mkdir(parents=True)
        file = folder/"Movie.2020.1080p.mkv"
        file.write_bytes(b"test")
        self.arr.decisions = [{"path": str(file), "movie": {"id": 1}, "quality": {"quality": {"id": 7}}, "rejections": []}]
        self.tick()
        self.assertEqual(len(self.arr.commands), 1)
        self.assertEqual(self.arr.commands[0]["files"][0]["movieId"], 1)
        self.arr.movies[0]["hasFile"] = True
        library = self.root/"library"/"Movie.mkv"
        library.parent.mkdir()
        library.write_bytes(b"test")
        self.arr.movies[0]["movieFile"] = {"path": str(library)}
        # The library must be outside the completed-download root.
        self.worker.downloads = self.root/"downloads"
        new_folder = self.worker.downloads/ident
        new_folder.parent.mkdir(parents=True)
        folder.rename(new_folder)
        self.tick()
        self.assertEqual(self.row()["state"], "done")
        self.assertFalse(new_folder.exists())
        self.assertTrue(library.is_file())

    def test_cleanup_retains_files_until_library_is_accessible(self):
        ident = self.reach_batch()
        folder = self.root/ident
        folder.mkdir(parents=True)
        source = folder/"Movie.2020.1080p.mkv"
        source.write_bytes(b"test")
        data = dict(json.loads(self.row()["data"]), command=123)
        self.worker.save(1, "importing", data)
        self.arr.movies[0].update(hasFile=True, movieFile={"path": str(self.root/"absent.mkv")})
        self.tick()
        self.assertEqual(self.row()["state"], "cleanup")
        self.assertTrue(source.is_file())

    def test_cleanup_never_deletes_after_failed_command(self):
        ident = self.reach_batch()
        data = dict(json.loads(self.row()["data"]), command=123)
        self.worker.save(1, "importing", data)
        self.arr.movies[0]["hasFile"] = True
        original = self.arr.call
        self.arr.call = lambda path, *args, **kwargs: {"status": "failed"} if path.endswith("command/123") else original(path, *args, **kwargs)
        self.tick()
        self.assertEqual(self.row()["state"], "cleanup")

    def test_cleanup_all_apps_retains_unrecognized_files_and_retries_partial_cleanup(self):
        import uuid
        for app in ("lidarr", "sonarr", "radarr"):
            with self.subTest(app=app):
                self.worker.app = app
                self.worker.downloads = self.root/"downloads"
                batch = str(uuid.uuid4())
                folder = self.worker.downloads/"arr-scripts"/app/batch
                folder.mkdir(parents=True)
                source = folder/"Song.flac"
                source.write_bytes(b"test")
                extra = folder/"personal.txt"
                extra.write_text("keep")
                library = self.root/"library.flac"
                library.write_bytes(b"test")
                def call(path, *args, **kwargs):
                    if "/command/" in path:
                        return {"status": "completed"}
                    if path.endswith("track"):
                        return [{"id": 3, "trackFileId": 4}]
                    if "/trackfile/" in path:
                        return {"path": str(library)}
                    kind = "movie" if app == "radarr" else "episode"
                    return {"hasFile": True, kind+"File": {"path": str(library)}}
                self.arr.call = call
                data = {"command": 123, "batch": batch, "files": [{"filename": "Album\\Song.flac", "size": 4}]}
                target = {"id": 1, "missingTrackIds": [3]}
                source.write_bytes(b"changed")
                with self.assertRaises(ValueError):
                    self.worker.cleanup_import(target, data)
                self.assertTrue(source.exists())
                source.write_bytes(b"test")
                self.worker.cleanup_import(target, data)
                self.worker.cleanup_import(target, data)
                self.assertFalse(source.exists())
                self.assertTrue(extra.exists())
                self.assertTrue(library.exists())

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
        self.assertEqual(len([p for p, _ in self.slsk.posts if p.endswith("searches")]), 1)

    def test_slow_queued_transfer_holds_slot_until_finished(self):
        self.reach_batch()
        self.arr.movies = [{**self.arr.movies[0], "id": number} for number in range(1, 10)]
        for _ in range(3):
            self.tick(86401)
        self.assertEqual(self.row()["state"], "downloading")
        self.assertEqual(len(self.slsk.searches), 1)
        self.arr.movies[0]["hasFile"] = True
        self.worker.save(1, "done", {})
        self.tick()
        self.assertEqual(len(self.slsk.searches), 2)
        self.assertEqual(self.worker.db.execute("SELECT count(*) FROM jobs WHERE state='searching'").fetchone()[0], 1)

    def reviewed_batch(self):
        batch = self.reach_batch()
        self.slsk.batches[batch]["transfers"][0]["state"] = "Completed, Succeeded"
        folder = self.root/batch
        folder.mkdir(parents=True)
        source = folder/"Movie.2020.1080p.mkv"
        source.write_bytes(b"test")
        data = json.loads(self.row()["data"])
        data["reason"] = "import rejected or identity mismatch"
        self.worker.save(1, "review", data)
        return folder,source,data

    def test_discard_removes_only_owned_files_and_does_not_redownload(self):
        folder,source,data = self.reviewed_batch()
        extra = folder/"personal.txt"
        extra.write_text("keep")
        self.worker.discard(1)
        self.assertFalse(source.exists())
        self.assertTrue(extra.exists())
        self.assertEqual(self.row()["state"], "discarded")
        self.tick(86401)
        self.assertEqual(len(self.slsk.searches), 1)

    def test_discard_retains_active_transfers(self):
        folder,source,data = self.reviewed_batch()
        self.slsk.batches[data["batch"]]["transfers"][0]["state"] = "Queued"
        with self.assertRaises(ValueError):
            self.worker.discard(1)
        self.assertTrue(source.exists())
        self.assertEqual(self.row()["state"], "review")

    def test_discard_retains_uncertain_or_running_import(self):
        folder,source,data = self.reviewed_batch()
        data["importTarget"] = {"id":1}
        self.worker.save(1,"review",data)
        with self.assertRaises(ValueError):
            self.worker.discard(1)
        data["command"] = 123
        self.worker.save(1,"review",data)
        original = self.arr.call
        self.arr.call = lambda path,*args,**kwargs: {"status":"started"} if path.endswith("command/123") else original(path,*args,**kwargs)
        with self.assertRaises(ValueError):
            self.worker.discard(1)
        self.assertTrue(source.exists())

    def test_discard_retains_changed_or_hardlinked_sources(self):
        import os
        folder,source,data = self.reviewed_batch()
        source.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.worker.discard(1)
        source.write_bytes(b"test")
        linked = self.root/"library.mkv"
        os.link(source,linked)
        with self.assertRaises(ValueError):
            self.worker.discard(1)
        self.assertTrue(source.exists())
        self.assertTrue(linked.exists())

    def test_discard_accepts_finished_partial_file_and_removes_empty_batch(self):
        folder,source,data = self.reviewed_batch()
        source.write_bytes(b"te")
        self.slsk.batches[data["batch"]]["transfers"][0]["state"] = "Completed, Errored"
        self.worker.discard(1)
        self.assertFalse(folder.exists())

    def test_new_batch_destination_is_directly_under_configured_root(self):
        batch = self.reach_batch()
        post = next(data for path,data in self.slsk.posts if path.endswith("batches"))
        self.assertEqual(post["options"]["destination"],batch)
        data = json.loads(self.row()["data"])
        self.assertEqual(self.worker.batch_folder(data),self.root/batch)

    def test_old_transfer_intent_keeps_its_existing_destination(self):
        import uuid
        batch = str(uuid.uuid4())
        self.assertEqual(self.worker.batch_folder({"batch":batch}),self.root/"arr-scripts"/"radarr"/batch)
        with self.assertRaises(ValueError):
            self.worker.batch_folder({"batch":batch,"destination":"../"+batch})

    def test_cancelled_discard_clears_client_removed_queued_job(self):
        folder,source,data = self.reviewed_batch()
        self.slsk.batches[data["batch"]]["transfers"][0]["state"] = "Queued"
        self.worker.save(1,"downloading",data)
        with self.assertRaises(ValueError):
            self.worker.discard(1)
        self.worker.discard(1,cancelled=True)
        self.assertFalse(folder.exists())
        self.assertEqual(self.row()["state"],"discarded")
        self.tick(86401)
        self.assertEqual(len(self.slsk.searches),1)

    def test_cancelled_flag_does_not_override_uncertain_import(self):
        folder,source,data = self.reviewed_batch()
        data["importTarget"] = {"id":1}
        self.worker.save(1,"review",data)
        with self.assertRaises(ValueError):
            self.worker.discard(1,cancelled=True)
        self.assertTrue(source.exists())

    def test_large_library_starts_searches_before_second_page_and_persists_cursor(self):
        calls = []
        original = self.arr.call
        def paginated(path, data=None, **query):
            if path.endswith("wanted/missing"):
                calls.append(query["page"])
                start = (query["page"]-1)*10+1
                return {"records": [{**self.arr.movies[0], "id": i} for i in range(start,start+10)], "totalRecords": 74851}
            if "/movie/" in path:
                return {**self.arr.movies[0], "id": int(path.rsplit("/",1)[1])}
            return original(path, data, **query)
        self.arr.call = paginated
        self.tick()
        self.assertEqual(calls, [1])
        self.assertEqual(len(self.slsk.searches), 1)
        self.assertEqual(self.worker.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 10)
        self.worker.db.close()
        self.worker = soulseek.Worker("radarr", self.arr, self.slsk, self.root, self.root, delay=0, clock=lambda: self.now)
        self.tick()
        self.assertEqual(calls, [1,2])
        self.assertEqual(self.row()["state"], "enqueueing")
        self.assertEqual(len(self.slsk.searches), 1)

    def test_page_absence_does_not_complete_waiting_job(self):
        self.worker.delay = 3600
        self.tick()
        original = self.arr.call
        def off_page(path, data=None, **query):
            if path.endswith("wanted/missing"):
                return {"records": [], "totalRecords": 0}
            return original(path,data,**query)
        self.arr.call = off_page
        self.tick(3601)
        self.assertEqual(self.row()["state"], "searching")

    def test_empty_or_final_page_wraps_cursor(self):
        with self.worker.db:
            self.worker.db.execute("UPDATE scan SET page=999")
        original = self.arr.call
        self.arr.call = lambda path, data=None, **query: {"records": [], "totalRecords": 0} if path.endswith("wanted/missing") else original(path,data,**query)
        self.tick()
        self.assertEqual(self.worker.db.execute("SELECT page FROM scan").fetchone()[0], 1)

    def test_failed_page_does_not_advance_cursor_or_submit(self):
        original = self.arr.call
        def fail(path,data=None,**query):
            if path.endswith("wanted/missing"):
                raise soulseek.ApiError(503)
            return original(path,data,**query)
        self.arr.call = fail
        with self.assertRaises(soulseek.ApiError):
            self.tick()
        self.assertEqual(self.worker.db.execute("SELECT page FROM scan").fetchone()[0], 1)
        self.assertFalse(self.slsk.posts)

    def test_streaming_miss_outside_current_page_is_prioritized(self):
        self.worker.app, self.worker.prefix = "lidarr", "api/v1/"
        self.worker.audio, self.worker.sources = True, "all"
        marker = self.root/"extended/soulseek/fallback/101"
        marker.parent.mkdir(parents=True)
        marker.touch()
        album = dict(id=101, title="Album", monitored=True, releaseDate="2020-01-01", artist={"id": 2,"artistName":"Artist","monitored":True}, releases=[{"id":9,"monitored":True}])
        def music(path,data=None,**query):
            if path.endswith("queue") or path.endswith("wanted/missing"):
                return {"records": [],"totalRecords":74851 if path.endswith("wanted/missing") else 0}
            if path.endswith("album/101"):
                return album.copy()
            if path.endswith("track"):
                return [{"id":3,"title":"Song","hasFile":False}]
            raise AssertionError(path)
        self.arr.call = music
        self.tick()
        job = self.worker.db.execute("SELECT state FROM jobs WHERE id=101").fetchone()
        self.assertEqual(job["state"], "searching")
        self.assertEqual(len(self.slsk.searches), 1)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        import uuid
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.arr = Arr()
        self.slsk = Soulseek()
        self.worker = soulseek.Worker("lidarr",self.arr,self.slsk,self.root,self.root/"downloads",delay=0,imports=self.root/"import")
        self.batch = str(uuid.uuid4())
        self.folder = self.worker.downloads/self.batch
        self.folder.mkdir(parents=True)
        self.source = self.folder/"Song.flac"
        self.source.write_bytes(b"test")
        self.target = dict(id=1,app="lidarr",title="Album",artist="Artist",artistId=2,tracks=[{"id":3,"title":"Song"}],missingTrackIds=[3],releaseDate="2020-01-01")
        self.data = dict(batch=self.batch, destination=self.batch, files=[{"filename":"Artist\\Album\\Song.flac","size":4}])
        with self.worker.db:
            self.worker.db.execute("INSERT INTO jobs(id,target,first_seen) VALUES(1,?,0)",(json.dumps(self.target),))
        original = self.arr.call
        def call(path,data=None,**query):
            if path.endswith("downloadclient"):
                return [{"enable":True,"implementation":"UsenetBlackhole","fields":[{"name":"watchFolder","value":str(self.worker.imports)}]}]
            return original(path,data,**query)
        self.arr.call = call

    def tearDown(self):
        self.worker.db.close()
        self.temp.cleanup()

    def test_completed_handoff_publishes_only_full_album_and_preserves_group(self):
        self.arr.decisions = [{"releaseGroup":"OriginalGroup","rejections":[{"reason":"identity mismatch"}]}]
        self.worker.handoff(self.target,self.data,self.folder)
        published = Path(self.data["handoff"])
        self.assertEqual(published.name,"Artist-Album (2020) [OriginalGroup]")
        self.assertEqual((published/"Song.flac").read_bytes(),b"test")
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.arr.commands,[{"name":"RefreshMonitoredDownloads"}])
        self.assertEqual(self.worker.db.execute("SELECT state FROM jobs").fetchone()[0],"handoff")

    def test_default_group_and_same_name_collision_never_overwrite(self):
        destination = self.worker.imports/"Artist-Album (2020) [Soulseek]"
        destination.mkdir(parents=True)
        personal = destination/"personal.txt"
        personal.write_text("keep")
        with self.assertRaises(ValueError):
            self.worker.handoff(self.target,self.data,self.folder)
        self.assertTrue(self.source.exists())
        self.assertEqual(personal.read_text(),"keep")

    def test_no_watched_client_retains_download(self):
        original = self.arr.call
        self.arr.call = lambda path,*args,**kwargs: [] if path.endswith("downloadclient") else original(path,*args,**kwargs)
        with self.assertRaises(ValueError):
            self.worker.handoff(self.target,self.data,self.folder)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.arr.commands)

    def test_cleanup_handoff_requires_final_library_and_preserves_unknown_files(self):
        self.worker.handoff(self.target,self.data,self.folder)
        published = Path(self.data["handoff"])
        unknown = published/"personal.txt"
        unknown.write_text("keep")
        library = self.root/"library"/"Song.flac"
        library.parent.mkdir()
        library.write_bytes(b"test")
        original = self.arr.call
        def call(path,*args,**kwargs):
            if path.endswith("track"):
                return [{"id":3,"trackFileId":4}]
            if path.endswith("trackfile/4"):
                return {"path":str(library)}
            return original(path,*args,**kwargs)
        self.arr.call = call
        self.worker.cleanup_handoff(self.target,self.data)
        self.assertTrue(library.exists())
        self.assertTrue(unknown.exists())
        self.assertFalse((published/"Song.flac").exists())

    def test_interrupted_publication_reconciles_without_overwriting_other_downloads(self):
        with patch.object(soulseek.os,"rename",side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.worker.handoff(self.target,self.data,self.folder)
        row = self.worker.db.execute("SELECT state,data FROM jobs WHERE id=1").fetchone()
        self.assertEqual(row["state"],"publishing")
        self.assertTrue(self.source.exists())
        self.worker.handoff(self.target,json.loads(row["data"]),self.folder)
        self.assertFalse(self.source.exists())
        self.assertEqual(len(self.arr.commands),1)

    def test_handoff_job_blocks_next_album_and_removal_does_not_republish(self):
        self.data["username"] = "test-peer"
        self.worker.handoff(self.target,self.data,self.folder)
        original = self.arr.call
        def call(path,data=None,**query):
            if path.endswith("wanted/missing"):
                return {"records":[dict(id=i,title="Album",monitored=True,artistId=2,releaseDate="2020-01-01") for i in (1,2)],"totalRecords":2}
            if path.endswith("artist/2"):
                return {"id":2,"artistName":"Artist","monitored":True}
            if "/album/" in path:
                return {"releases":[{"id":9,"monitored":True}]}
            if path.endswith("track"):
                return [{"id":3,"title":"Song"}]
            return original(path,data,**query)
        self.arr.call = call
        with self.worker.db:
            self.worker.db.execute("UPDATE jobs SET next_run=0")
        self.worker.tick()
        self.assertFalse(self.slsk.searches)
        published = Path(self.data["handoff"])
        for file in published.iterdir():
            file.unlink()
        published.rmdir()
        with self.worker.db:
            self.worker.db.execute("UPDATE jobs SET next_run=0")
        self.worker.tick()
        self.assertEqual(self.worker.db.execute("SELECT state FROM jobs WHERE id=1").fetchone()[0],"failure_cleanup")
        self.assertFalse(published.exists())


class BackgroundTests(unittest.TestCase):
    setUp = WorkerTests.setUp
    tearDown = WorkerTests.tearDown
    tick = WorkerTests.tick
    row = WorkerTests.row

    def seed(self, alternatives=False):
        self.slsk.client,self.slsk.version = "nicotine","0.1.3"
        self.tick()
        self.tick()
        self.tick()
        data = json.loads(self.row()["data"])
        target = json.loads(self.row()["target"])
        if alternatives:
            data["candidates"] = [dict(username="other",files=data["files"])]
        self.slsk.batches[data["batch"]]["peerOffline"] = True
        data["offlineSince"] = self.now
        self.worker.save(1,"downloading",data)
        return target,data

    def test_offline_retains_original_and_launches_alternative_after_15_minutes(self):
        target,data = self.seed(alternatives=True)
        self.tick(899)
        self.assertEqual(self.row()["state"],"downloading")
        self.tick(61)
        self.assertEqual(self.row()["state"],"enqueueing")
        self.assertEqual(json.loads(self.row()["data"])["username"],"other")
        self.assertEqual(self.worker.db.execute("SELECT count(*) FROM background_batches").fetchone()[0],1)
        self.assertFalse(any(path.endswith(("/clear","/cancel")) for path,_ in self.slsk.posts))

    def test_original_finishes_and_replacement_is_preserved_until_import(self):
        target,data = self.seed(alternatives=True)
        self.worker.background_batch(1,target,data)
        self.tick()
        replacement = json.loads(self.row()["data"])["batch"]
        batch = self.slsk.batches[data["batch"]]
        batch["peerOffline"] = False
        for file in batch["transfers"]: file["state"] = "Completed, Succeeded"
        self.worker.monitor_background([])
        self.assertEqual(self.row()["state"],"downloaded")
        self.assertEqual(json.loads(self.row()["data"])["batch"],data["batch"])
        self.assertEqual(self.worker.db.execute("SELECT batch FROM background_batches").fetchone()[0],replacement)
        self.assertFalse(any(path.endswith("/cancel") for path,_ in self.slsk.posts))

    def test_background_expiry_uses_24_hours_and_30_day_exclusion(self):
        target,data = self.seed()
        self.worker.background_batch(1,target,data)
        self.now += 86399
        self.worker.monitor_background([])
        self.assertEqual(self.worker.db.execute("SELECT count(*) FROM background_batches").fetchone()[0],1)
        self.now += 1
        self.worker.monitor_background([])
        self.assertEqual(self.worker.db.execute("SELECT count(*) FROM background_batches").fetchone()[0],0)
        expiry = self.worker.db.execute("SELECT expires FROM failed_candidates").fetchone()[0]
        self.assertEqual(expiry,self.now+30*86400)
        self.assertEqual(self.row()["state"],"waiting")
        self.assertTrue(self.slsk.batches[data["batch"]]["cleared"])

    def test_background_timeout_api_failure_keeps_journal_and_files(self):
        target,data=self.seed()
        self.worker.background_batch(1,target,data)
        folder=self.root/data["batch"]
        folder.mkdir()
        source=folder/"Movie.2020.1080p.mkv"
        source.write_bytes(b"test")
        self.now += 86400
        original=self.slsk.call
        def unavailable(path,*args,**kwargs):
            if path.endswith("/cancel"):
                raise soulseek.ApiError()
            return original(path,*args,**kwargs)
        with patch.object(self.slsk,"call",side_effect=unavailable):
            self.worker.monitor_background([])
        self.assertTrue(source.exists())
        self.assertEqual(self.worker.db.execute("SELECT count(*) FROM background_batches").fetchone()[0],1)

    def test_returning_peer_resets_offline_clock(self):
        target,data = self.seed()
        self.worker.background_batch(1,target,data)
        self.now += 86000
        self.slsk.batches[data["batch"]]["peerOffline"] = False
        self.worker.monitor_background([])
        saved=json.loads(self.worker.db.execute("SELECT data FROM background_batches").fetchone()[0])
        self.assertNotIn("offlineSince",saved)
        self.slsk.batches[data["batch"]]["peerOffline"] = True
        self.now += 1000
        self.worker.monitor_background([])
        saved=json.loads(self.worker.db.execute("SELECT data FROM background_batches").fetchone()[0])
        self.assertEqual(saved["offlineSince"],self.now)

    def test_background_journal_survives_restart_and_bounds_candidates(self):
        target,data = self.seed()
        self.worker.background_batch(1,target,data)
        self.worker.db.close()
        self.worker=soulseek.Worker("radarr",self.arr,self.slsk,self.root,self.root,clock=lambda:self.now,max_batches=1)
        self.assertFalse(self.worker.batch_room(1))
        self.worker.next_candidate(1,dict(search="search",candidates=[dict(username="other",files=data["files"])]))
        self.assertEqual(self.row()["state"],"candidate_wait")
        self.assertEqual(len(self.slsk.batches),1)

    def test_missing_library_file_does_not_cancel_background(self):
        target,data = self.seed()
        self.worker.background_batch(1,target,data)
        self.arr.movies[0].update(hasFile=True,movieFile={"path":str(self.root/"missing-library.mkv")})
        self.worker.monitor_background([])
        self.assertFalse(any(path.endswith("/cancel") for path,_ in self.slsk.posts))

    def test_verified_library_cancels_and_clears_redundant_copy(self):
        target,data = self.seed()
        self.worker.background_batch(1,target,data)
        library=self.root.parent/("library-"+data["batch"]+".mkv")
        library.write_bytes(b"library")
        try:
            self.arr.movies[0].update(hasFile=True,movieFile={"path":str(library)})
            self.worker.monitor_background([])
            self.assertTrue(library.exists())
            self.assertEqual(self.row()["state"],"done")
            self.assertTrue(self.slsk.batches[data["batch"]]["cleared"])
            self.assertEqual(self.worker.db.execute("SELECT count(*) FROM background_batches").fetchone()[0],0)
        finally:
            library.unlink(missing_ok=True)


class ClientTests(unittest.TestCase):
    def test_nicotine_adapter_routes_to_own_api_version(self):
        client = soulseek.SoulseekApi("http://example.invalid:12339", "test-key", "nicotine")
        with patch.object(soulseek.Api, "call", return_value={}) as call:
            client.call("api/v0/searches", {"searchText": "Song"})
        call.assert_called_once_with("api/v1/searches", {"searchText": "Song"})


if __name__ == "__main__":
    unittest.main()
