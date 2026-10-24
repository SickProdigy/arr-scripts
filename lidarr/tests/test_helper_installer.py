import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_musicbrainz_genres_wrapper import BASH, ROOT, shell_path


class HelperInstallerTests(unittest.TestCase):
    def run_installer(self, raw="https://forge.example.test/owner/arr-scripts/raw/branch/main", overrides=None, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "trace"
            for name in ("uv", "python3", "musicbrainz-genres", "musicbrainz-ratings", "musicbrainz-auth", "sonicratings"):
                (root / name).write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$0" "$@" >> "$TRACE"\n'
                                        'if [ "${0##*/}" = "python3" ]; then cat >/dev/null; fi\n'
                                        'exit "${FAKE_EXIT:-0}"\n', encoding="utf-8")
            environment = {**os.environ, "PATH": shell_path(root) + ":/usr/bin:/bin",
                           "TRACE": shell_path(trace), "arrScriptsRawBase": raw,
                           "FAKE_EXIT": "1" if fail else "0"}
            for key in ("ARR_HELPER_REPOSITORY_BASE", "ARR_SONICRATINGS_ENGINE_SOURCE", "ARR_MUSICBRAINZ_HELPER_SOURCE", "ARR_SONICRATINGS_SOURCE"):
                environment.pop(key, None)
            environment.update(overrides or {})
            for name in ("uv", "python3", "musicbrainz-genres", "musicbrainz-ratings", "musicbrainz-auth", "sonicratings"):
                subprocess.run([BASH, "-c", 'chmod +x "$1"', "--", shell_path(root / name)],
                               env=environment, check=True, capture_output=True)
            result = subprocess.run([BASH, str(ROOT / "InstallHelpers.bash")],
                                    env=environment, capture_output=True, text=True)
            return result.returncode, trace.read_text().splitlines() if trace.exists() else []

    def test_dependencies_default_to_pinned_pypi_packages(self):
        code, trace = self.run_installer()
        self.assertEqual(code, 0)
        self.assertIn("musicbrainz-helper==0.5.1", trace)
        self.assertIn("sonicratings==1.4.0", trace)
        self.assertFalse(any("git+" in line for line in trace))
        self.assertFalse(any("sonicratings-engine" in line for line in trace))
        self.assertIn("--no-deps", trace)
        self.assertIn("--system", trace)

    def test_arr_source_owner_does_not_redirect_helper_dependencies(self):
        code, trace = self.run_installer(raw="https://raw.githubusercontent.com/owner/arr-scripts/main")
        self.assertEqual(code, 0)
        self.assertIn("musicbrainz-helper==0.5.1", trace)

    def test_explicit_local_sources_are_supported(self):
        code, trace = self.run_installer(raw="https://example.test/unusual", overrides={
            "ARR_HELPER_REPOSITORY_BASE": "https://forge.example.test/owner",
            "ARR_SONICRATINGS_ENGINE_SOURCE": "/sources/sonicratings engine",
            "ARR_MUSICBRAINZ_HELPER_SOURCE": "/sources/musicbrainz helper",
            "ARR_SONICRATINGS_SOURCE": "/sources/sonicratings"})
        self.assertEqual(code, 0)
        self.assertNotIn("/sources/sonicratings engine", trace)
        self.assertIn("/sources/musicbrainz helper", trace)
        self.assertIn("/sources/sonicratings", trace)

    def test_obsolete_repository_base_is_ignored_and_failed_install_fails_closed(self):
        code, trace = self.run_installer(overrides={"ARR_HELPER_REPOSITORY_BASE": "https://forge.example.test/owner"})
        self.assertEqual(code, 0)
        self.assertIn("musicbrainz-helper==0.5.1", trace)
        self.assertIn("sonicratings==1.4.0", trace)
        self.assertFalse(any("forge.example.test" in line for line in trace))
        code, trace = self.run_installer(fail=True)
        self.assertEqual(code, 1)
        self.assertFalse(any(line.endswith("/python3") for line in trace))


if __name__ == "__main__":
    unittest.main()
