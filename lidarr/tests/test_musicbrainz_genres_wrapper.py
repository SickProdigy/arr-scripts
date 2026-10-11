"""Exercise the real event wrapper using local config and a fake Python executable."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
BASH = str(Path("C:/Program Files/Git/bin/bash.exe")) if os.name == "nt" else shutil.which("bash")


def shell_path(path):
    value = Path(path).as_posix()
    return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" else value


class GenreWrapperTests(unittest.TestCase):
    def run_wrapper(self, enabled="true", beets="false", event="Download", arguments=(), album="123", fail=False,
                    username="sickprodigy", password="fake-password", script_name="MusicBrainzGenres.bash",
                    helper_name="musicbrainz-genres", extra_config=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extended = root / "extended"
            extended.mkdir()
            tools = root / "bin"
            tools.mkdir()
            trace = root / "trace"
            (root / "extended.conf").write_text(
                f'enableMusicBrainzHelper="{enabled}"\nenableBeetsTagging="{beets}"\n'
                f'musicBrainzUsername="{username}"\nmusicBrainzToken="{password}"\n' + extra_config, encoding="utf-8")
            (extended / "functions").write_text(
                'getArrAppInfo() { arrUrl="http://localhost:8686"; arrApiKey="fake-key"; }\n', encoding="utf-8")
            (tools / helper_name).write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$TRACE"\n'
                'printf "%s\\n" "${LIDARR_URL:-}" "${MB_USERNAME:-}" "${RATINGS_SUBMIT:-}" "${RATINGS_INTERVAL:-}" "${SUBSONIC_URL:-}" "${SUBSONIC_USERNAME:-}" >> "$TRACE"\nexit "${FAKE_EXIT:-0}"\n', encoding="utf-8")
            script = root / "wrapper.bash"
            script.write_text((ROOT / script_name).read_text().replace(
                "/config/", shell_path(root) + "/"), encoding="utf-8")
            environment = {**os.environ, "TRACE": shell_path(trace), "FAKE_EXIT": "1" if fail else "0",
                           "lidarr_eventtype": event, "lidarr_album_id": album,
                           "PATH": shell_path(tools) + ":/usr/bin:/bin"}
            subprocess.run([BASH, "-c", 'chmod +x "$1"', "--", shell_path(tools / helper_name)],
                           check=True, env=environment, capture_output=True)
            result = subprocess.run([BASH, shell_path(script), *arguments], env=environment,
                                    capture_output=True, text=True)
            return result.returncode, trace.read_text().splitlines() if trace.exists() else []

    def test_test_event_and_disabled_feature_do_not_start_work(self):
        self.assertEqual(self.run_wrapper(event="Test"), (0, []))
        self.assertEqual(self.run_wrapper(enabled="false"), (0, []))

    def test_beets_queues_only_after_tagging(self):
        self.assertEqual(self.run_wrapper(beets="true"), (0, []))
        code, trace = self.run_wrapper(beets="true", arguments=("--after-beets",))
        self.assertEqual(code, 0)
        self.assertIn("enqueue", trace)
        self.assertIn("embedded-beets", trace)
        self.assertIn("123", trace)
        self.assertIn("http://localhost:8686", trace)

    def test_provider_tags_queue_without_beets(self):
        code, trace = self.run_wrapper()
        self.assertEqual(code, 0)
        self.assertIn("embedded-provider", trace)

    def test_enabled_worker_submits_using_config_credentials_without_password_arguments(self):
        code, trace = self.run_wrapper(arguments=("--work",))
        self.assertEqual(code, 0)
        self.assertIn("work", trace)
        self.assertIn("--submit", trace)
        self.assertIn("artist", trace)
        self.assertNotIn("--credentials-file", trace)
        self.assertIn("--account-profile", trace)
        self.assertIn("sickprodigy", trace)
        self.assertNotIn("fake-password", trace)

    def test_worker_requires_credentials_and_manual_preview_never_submits(self):
        self.assertEqual(self.run_wrapper(arguments=("--work",), password=""), (0, []))
        code, trace = self.run_wrapper(arguments=("--work", "--preview"))
        self.assertEqual(code, 0)
        self.assertIn("work", trace)
        self.assertNotIn("--submit", trace)

    def test_lidarr_retag_event_queues_even_when_beets_is_enabled(self):
        code, trace = self.run_wrapper(beets="true", event="TrackRetag")
        self.assertEqual(code, 0)
        self.assertIn("enqueue", trace)
        self.assertIn("--account-profile", trace)

    def test_library_scan_needs_no_album_and_reports_batch_failure(self):
        code, trace = self.run_wrapper(beets="true", arguments=("--library",), album="")
        self.assertEqual(code, 0)
        self.assertIn("scan", trace)
        self.assertNotIn("--album-id", trace)
        self.assertIn("sickprodigy", trace)
        code, _ = self.run_wrapper(arguments=("--library",), album="", fail=True)
        self.assertEqual(code, 1)

    def test_queue_failure_does_not_fail_import_but_worker_reports_failure(self):
        code, trace = self.run_wrapper(fail=True)
        self.assertEqual(code, 0)
        self.assertIn("enqueue", trace)
        code, _ = self.run_wrapper(fail=True, arguments=("--work",))
        self.assertEqual(code, 1)

    def test_invalid_album_does_not_queue(self):
        self.assertEqual(self.run_wrapper(album="../invalid"), (0, []))


if __name__ == "__main__":
    unittest.main()
