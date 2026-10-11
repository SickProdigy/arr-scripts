import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
BASH = shutil.which("bash")
if os.name == "nt":
    BASH = r"C:\Program Files\Git\bin\bash.exe"


@unittest.skipUnless(BASH and Path(BASH).is_file(), "Bash is required")
class TdarrTests(unittest.TestCase):
    def run_script(self, name, enabled, video=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "radarr"
            folder.mkdir()
            files = ["subtitle.srt", "cover.jpg", "release.nfo"]
            if video:
                files += ["Movie.mp4", "Movie.mkv"]
            for filename in files:
                (folder / filename).write_bytes(b"keep-this-file")
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
            source = (ROOT / name).read_text()
            if name == "video.bash":
                source = source[source.index("function Configuration"):source.rindex('Main "$@"')]
                entry = 'Main "$TEST_DOWNLOAD" unused Job unused radarr'
            else:
                source = source[source.index("installDependencies ()"):source.rindex('MAIN "$1"')]
                entry = 'MAIN "$TEST_DOWNLOAD"'
            harness = root / "harness.bash"
            # Use real control flow and file checks; any processing path must
            # announce itself rather than invoking media tools or deleting data.
            overrides = "\n".join(f'{fn} () {{ echo CALLED:{fn}; }}' for fn in (
                "Configuration", "VideoLanguageCheck", "VideoSmaProcess", "DeleteLocalArtwork",
                "MkvMerge", "MkvPropEdit", "Cleaner", "installDependencies", "VerifyApiAccess", "ArrDownloadInfo"))
            harness.write_text(source + "\n" + overrides + "\n" +
                              'log () { echo "$1"; }\nlogfileSetup () { logFileName="test.log"; }\n'
                              'arrApiKeySelect () { arrApp=Radarr; arrUrl=http://test; arrApiKey=test-key; }\n'
                              'arrRefreshMonitoredDownloads () { echo REFRESH; }\n'
                              'set -e\nenableSma=true\nSAB_NZO_ID=test\n' + entry + "\n", newline="\n")
            environment = dict(os.environ, letTdarrHandleVideo="true" if enabled else "false",
                               TEST_DOWNLOAD=folder.as_posix(), dockerPath=root.as_posix())
            result = subprocess.run([BASH, harness.as_posix()], env=environment,
                                    capture_output=True, text=True, timeout=20)
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
            return result, before, after

    def test_tdarr_preserves_all_download_files_and_skips_processing(self):
        for script in ("video.bash", "video2.bash"):
            with self.subTest(script=script):
                result, before, after = self.run_script(script, True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(before, after)
                self.assertNotIn("CALLED:", result.stdout)
                if script == "video2.bash":
                    self.assertIn("REFRESH", result.stdout)

    def test_tdarr_rejects_missing_video_without_deleting_sidecars(self):
        for script in ("video.bash", "video2.bash"):
            with self.subTest(script=script):
                result, before, after = self.run_script(script, True, video=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before, after)
                self.assertNotIn("CALLED:", result.stdout)

    def test_disabled_toggle_keeps_existing_processing(self):
        for script in ("video.bash", "video2.bash"):
            with self.subTest(script=script):
                result, _, _ = self.run_script(script, False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("CALLED:VideoLanguageCheck", result.stdout)
                expected = "VideoSmaProcess" if script == "video.bash" else "MkvMerge"
                self.assertIn("CALLED:" + expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
