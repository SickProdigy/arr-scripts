import importlib.util
from pathlib import Path
import os
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_musicbrainz_genres_wrapper import BASH, ROOT, shell_path

spec = importlib.util.spec_from_file_location("beets_lyrics", ROOT / "python/BeetsLyrics.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class Item(dict):
    def __init__(self, path, identity=1, **fields):
        super().__init__(fields)
        self.path = os.fsencode(path)
        self.id = identity


class LyricsTests(unittest.TestCase):
    def test_supported_lyric_tags_including_synced_id3_are_detected(self):
        for key, value in (("LYRICS", ["words"]), ("SYNCEDLYRICS", ["[00:01] words"]),
                           ("USLT::eng", SimpleNamespace(text="words")),
                           ("SYLT::eng", SimpleNamespace(text=[("words", 1000)])),
                           ("\xa9lyr", ["words"])):
            with self.subTest(key=key):
                self.assertTrue(helper.embedded_lyrics("file", lambda _: SimpleNamespace(tags={key: value})))
        self.assertFalse(helper.embedded_lyrics("file", lambda _: SimpleNamespace(tags={"lyrics": [" "]})))

    def test_existing_embedded_database_and_sidecar_lyrics_skip_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ("既存.flac", "database.mp3", "sidecar.m4a")]
            paths[2].with_suffix(".lrc").write_text("[00:01] local lyrics", encoding="utf-8")
            items = [Item(paths[0]), Item(paths[1], lyrics="database lyrics"), Item(paths[2])]
            read = lambda path: SimpleNamespace(tags={"LYRICS": ["existing"]} if path == paths[0] else {})
            run, embed = Mock(), Mock()
            self.assertEqual(helper.process(items, root, "config", "library", read, embed, run), 0)
            run.assert_not_called()
            self.assertEqual(embed.call_count, 2)
            self.assertTrue(paths[2].with_suffix(".lrc").exists())

    def test_multidisc_paths_are_selected_by_id_without_matching_other_albums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Album with spaces 音楽"
            root.mkdir()
            items = [Item(root / "Disc 1/song.flac", 1), Item(root / "Disc 2/song.opus", 2),
                     Item(root.parent / "another/song.flac", 3)]
            run = Mock(return_value=SimpleNamespace(returncode=0))
            helper.process(items, root, "my config", "my library", lambda _: SimpleNamespace(tags={}), Mock(), run)
            self.assertEqual(run.call_count, 2)
            self.assertEqual([call.args[0][-1] for call in run.call_args_list], ["id:1", "id:2"])

    def test_success_no_result_failure_and_partial_completion_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items = [Item(root / str(i), i) for i in range(1, 4)]
            tags = {}
            def run(command, **kwargs):
                if command[-1] == "id:1":
                    tags[root / "1"] = {"LYRICS": ["completed"]}
                return SimpleNamespace(returncode=1 if command[-1] == "id:2" else 0)
            self.assertEqual(helper.process(items, root, "config", "library",
                                           lambda p: SimpleNamespace(tags=tags.get(p, {})), Mock(), run), 1)
            self.assertEqual(tags[root / "1"], {"LYRICS": ["completed"]})

    def test_unreadable_files_and_instrumentals_skip_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = Mock()
            items = [Item(root / "instrumental", lyrics_instrumental=True)]
            helper.process(items, root, "config", "library", lambda _: SimpleNamespace(tags={}), Mock(), run)
            self.assertEqual(helper.process(items, root, "config", "library", lambda _: None, Mock(), run), 1)
            run.assert_not_called()

    def wrapper(self, enabled="true", budget="1s", body="exit 0"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "extended.conf").write_text(f'enableBeetsLyrics="{enabled}"\nbeetsLyricsTimeout="{budget}"\n')
            script = root / "wrapper.bash"
            script.write_text((ROOT / "BeetsLyrics.bash").read_text().replace("/config/", shell_path(root) + "/"))
            binary = root / "python3"
            binary.write_text('#!/usr/bin/env bash\n' + body + '\n')
            environment = {**os.environ, "PATH": shell_path(root) + ":/usr/bin:/bin"}
            subprocess.run([BASH, "-c", 'chmod +x "$1"', "--", shell_path(binary)], env=environment, check=True)
            start = time.monotonic()
            result = subprocess.run([BASH, str(script), "config", "library", "album"],
                                    env=environment, capture_output=True, text=True, timeout=8)
            return result, time.monotonic() - start

    def test_disabled_and_invalid_budget_make_no_requests(self):
        for enabled, budget in (("false", "1s"), ("true", "invalid")):
            result, _ = self.wrapper(enabled, budget, 'echo UNEXPECTED')
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("UNEXPECTED", result.stdout)

    def test_hung_and_repeated_rate_limited_providers_are_bounded_and_nonfatal(self):
        for body in ('sleep 30', 'echo "completed first track"; while true; do echo "HTTP 429"; sleep 0.1; done'):
            result, elapsed = self.wrapper(body=body)
            self.assertEqual(result.returncode, 0)
            self.assertLess(elapsed, 5)
            self.assertIn("timed out", result.stdout)
            if "429" in body:
                self.assertIn("completed first track", result.stdout)
                self.assertIn("HTTP 429", result.stdout)

    def test_provider_errors_are_logged_and_nonfatal(self):
        result, _ = self.wrapper(body='echo "provider unavailable"; exit 2')
        self.assertEqual(result.returncode, 0)
        self.assertIn("exit status 2", result.stdout)

    def test_metadata_failure_skips_lyrics_and_lyric_failure_does_not_stop_import(self):
        source = (ROOT / "BeetsTagger.bash").read_text()
        start = source.index("\tBEETSDIR=/config/extended beet ")
        stop = source.index("\t# Fix tags", start)
        fragment = source[start:stop]
        self.assertIn("-P lyrics", fragment)
        for metadata_status in (0, 1):
            with self.subTest(metadata_status=metadata_status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "logs").mkdir()
                (root / "extended").mkdir()
                (root / "extended/BeetsLyrics.bash").write_text('echo LYRICS_CALLED; exit 1\n')
                binary = root / "beet"
                binary.write_text(f'#!/usr/bin/env bash\nexit {metadata_status}\n')
                env = {**os.environ, "PATH": shell_path(root) + ":/usr/bin:/bin"}
                subprocess.run([BASH, "-c", 'chmod +x "$1"', "--", shell_path(binary)], env=env, check=True)
                script = root / "run.bash"
                script.write_text('logFileName=test.log\n' + fragment.replace("/config/", shell_path(root) + "/") + '\necho IMPORT_CONTINUES\n')
                result = subprocess.run([BASH, str(script), "album"], env=env, capture_output=True, text=True, check=True)
                self.assertIn("IMPORT_CONTINUES", result.stdout)
                self.assertEqual("LYRICS_CALLED" in result.stdout, metadata_status == 0)


if __name__ == "__main__":
    unittest.main()
