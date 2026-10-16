"""Recovery of retained files must not starve unrelated searches."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_soulseek as fixtures

soulseek = fixtures.soulseek


class CleanupRecoveryTests(unittest.TestCase):
    setUp = fixtures.WorkerTests.setUp
    tearDown = fixtures.WorkerTests.tearDown
    tick = fixtures.WorkerTests.tick
    row = fixtures.WorkerTests.row

    def seed(self):
        self.tick()
        batch = str(soulseek.uuid.uuid4())
        folder = self.root / batch
        folder.mkdir()
        source = folder / "Movie.mkv"
        source.write_bytes(b"oversize")
        data = dict(batch=batch, destination=batch, username="peer",
                    files=[dict(filename="Movie.mkv", size=4)], candidates=[])
        self.slsk.client, self.slsk.version = "nicotine", "0.1.3"
        self.slsk.batches[batch] = {"transfers": [{**data["files"][0], "state": "Completed, Errored"}]}
        self.worker.fail_candidate(1, data, "failed transfer")
        return source, data

    def test_oversized_file_survives_bounded_cleanup_across_restart(self):
        source, data = self.seed()
        self.tick()
        self.assertEqual(json.loads(self.row()["data"])["cleanupAttempts"], 1)
        self.worker.db.close()
        self.worker = soulseek.Worker("radarr", self.arr, self.slsk, self.root,
                                     self.root, delay=0, clock=lambda: self.now)
        self.tick()
        self.tick()
        self.assertEqual(self.row()["state"], "review")
        self.assertEqual(source.read_bytes(), b"oversize")
        self.assertTrue(self.worker.excluded(1, data))
        self.assertFalse(self.worker.excluded(1, {**data, "username": "another-peer"}))

    def test_failed_cleanup_does_not_block_another_search(self):
        self.seed()
        self.arr.movies.append({**self.arr.movies[0], "id": 2, "title": "Another Movie"})
        self.tick()
        row = self.worker.db.execute("SELECT state FROM jobs WHERE id=2").fetchone()
        self.assertEqual(row["state"], "searching")
        self.assertEqual(self.row()["state"], "failure_cleanup")


class HandoffRecoveryTests(unittest.TestCase):
    setUp = fixtures.HandoffTests.setUp
    tearDown = fixtures.HandoffTests.tearDown

    def tick(self):
        with patch.object(self.worker, "discover", return_value=([{"id": 1}], 1, 1, 1)), \
             patch.object(self.worker, "targets", return_value=iter([self.target])):
            self.worker.tick()

    def test_removed_handoff_cleans_missing_batch_and_keeps_candidate_exclusion(self):
        self.data["username"] = "test-peer"
        self.worker.handoff(self.target, self.data, self.folder)
        published = Path(self.data["handoff"])
        for path in published.iterdir():
            path.unlink()
        published.rmdir()
        self.worker.slsk.client, self.worker.slsk.version = "nicotine", "0.1.3"
        original = self.slsk.call
        def call(path, data=None, **query):
            if path.endswith(("/cancel", "/clear")):
                raise soulseek.ApiError(404)
            return original(path, data, **query)
        with patch.object(self.slsk, "call", side_effect=call):
            with self.worker.db:
                self.worker.db.execute("UPDATE jobs SET next_run=0")
            self.tick()
            self.assertEqual(self.worker.db.execute("SELECT state FROM jobs WHERE id=1").fetchone()[0], "failure_cleanup")
            self.tick()
        self.assertTrue(self.worker.excluded(1, self.data))
        self.assertEqual(self.worker.db.execute("SELECT state FROM jobs WHERE id=1").fetchone()[0], "waiting")
        self.assertFalse(published.exists())

    def test_stale_handoff_retains_files_and_excludes_only_its_candidate(self):
        self.worker.clock = lambda: 2000000000
        self.data["username"] = "test-peer"
        self.worker.handoff(self.target, self.data, self.folder)
        published = Path(self.data["handoff"])
        before = {p.name: p.read_bytes() for p in published.iterdir()}
        self.worker.clock = lambda: 2000003601
        with patch.object(self.worker, "discover", return_value=([{"id": 1}], 1, 1, 1)), \
             patch.object(self.worker, "targets", return_value=iter([self.target])):
            self.worker.tick()
        row = self.worker.db.execute("SELECT state,data FROM jobs WHERE id=1").fetchone()
        self.assertEqual(row["state"], "review")
        self.assertEqual({p.name: p.read_bytes() for p in published.iterdir()}, before)
        self.assertTrue(self.worker.excluded(1, self.data))
        self.assertFalse(self.worker.excluded(2, self.data))

    def test_active_handoff_is_not_timed_out(self):
        self.worker.clock = lambda: 2000000000
        self.worker.handoff(self.target, self.data, self.folder)
        self.worker.clock = lambda: 2000003601
        queued = [{"outputPath": self.data["handoff"], "status": "downloading"}]
        with patch.object(self.worker, "queued", return_value=queued):
            self.tick()
        row = self.worker.db.execute("SELECT state FROM jobs WHERE id=1").fetchone()
        self.assertEqual(row["state"], "handoff")

    def test_expired_handoff_releases_search_gate_in_same_pass(self):
        self.worker.clock = lambda: 2000000000
        self.data["username"] = "test-peer"
        self.worker.handoff(self.target, self.data, self.folder)
        self.worker.clock = lambda: 2000003601
        another = {**self.target, "id": 2, "query": "Another Album"}
        with patch.object(self.worker, "discover", return_value=([{"id": 1}, {"id": 2}], 1, 2, 1)), \
             patch.object(self.worker, "targets", return_value=iter([self.target, another])), \
             patch.object(self.worker, "ready", return_value=True):
            self.worker.tick()
        row = self.worker.db.execute("SELECT state FROM jobs WHERE id=2").fetchone()
        self.assertEqual(row["state"], "searching")


if __name__ == "__main__":
    unittest.main()
