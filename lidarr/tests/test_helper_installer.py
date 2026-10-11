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
            for name in ("uv", "python3", "musicbrainz-genres", "musicbrainz-ratings"):
                (root / name).write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$0" "$@" >> "$TRACE"\n'
                                        'if [ "${0##*/}" = "python3" ]; then cat >/dev/null; fi\n'
                                        'exit "${FAKE_EXIT:-0}"\n', encoding="utf-8")
            environment = {**os.environ, "PATH": shell_path(root) + ":/usr/bin:/bin",
                           "TRACE": shell_path(trace), "arrScriptsRawBase": raw,
                           "FAKE_EXIT": "1" if fail else "0"}
            for key in ("ARR_HELPER_REPOSITORY_BASE", "ARR_SONICRATINGS_ENGINE_SOURCE", "ARR_MUSICBRAINZ_HELPER_SOURCE"):
                environment.pop(key, None)
            environment.update(overrides or {})
            for name in ("uv", "python3", "musicbrainz-genres", "musicbrainz-ratings"):
                subprocess.run([BASH, "-c", 'chmod +x "$1"', "--", shell_path(root / name)],
                               env=environment, check=True, capture_output=True)
            result = subprocess.run([BASH, str(ROOT / "InstallHelpers.bash")],
                                    env=environment, capture_output=True, text=True)
            return result.returncode, trace.read_text().splitlines() if trace.exists() else []

    def test_dependencies_default_to_public_github_even_when_arr_uses_another_forge(self):
        code, trace = self.run_installer()
        self.assertEqual(code, 0)
        self.assertIn("git+https://github.com/SickProdigy/musicbrainz-helper.git@v0.3.0", trace)
        self.assertIn("git+https://github.com/SickProdigy/SonicRatings.git@engine-v0.1.0", trace)
        self.assertIn("--no-deps", trace)
        self.assertIn("--system", trace)

    def test_arr_source_owner_does_not_redirect_helper_dependencies(self):
        code, trace = self.run_installer(raw="https://raw.githubusercontent.com/owner/arr-scripts/main")
        self.assertEqual(code, 0)
        self.assertIn("git+https://github.com/SickProdigy/musicbrainz-helper.git@v0.3.0", trace)

    def test_local_sources_and_custom_base_are_supported(self):
        code, trace = self.run_installer(raw="https://example.test/unusual", overrides={
            "ARR_HELPER_REPOSITORY_BASE": "https://forge.example.test/owner",
            "ARR_SONICRATINGS_ENGINE_SOURCE": "/sources/sonicratings engine",
            "ARR_MUSICBRAINZ_HELPER_SOURCE": "/sources/musicbrainz helper"})
        self.assertEqual(code, 0)
        self.assertIn("/sources/sonicratings engine", trace)
        self.assertIn("/sources/musicbrainz helper", trace)

    def test_custom_repository_base_is_honored_and_failed_install_fails_closed(self):
        code, trace = self.run_installer(overrides={"ARR_HELPER_REPOSITORY_BASE": "https://forge.example.test/owner"})
        self.assertEqual(code, 0)
        self.assertIn("git+https://forge.example.test/owner/SonicRatings.git@engine-v0.1.0", trace)
        code, trace = self.run_installer(fail=True)
        self.assertEqual(code, 1)
        self.assertFalse(any(line.endswith("/python3") for line in trace))


if __name__ == "__main__":
    unittest.main()
