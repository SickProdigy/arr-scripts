import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
BASH = os.environ.get("BASH_EXE") or shutil.which("bash")
if os.name == "nt" and Path("C:/Program Files/Git/bin/bash.exe").exists():
    BASH = "C:/Program Files/Git/bin/bash.exe"


@unittest.skipUnless(BASH, "Bash required")
class WrapperTests(unittest.TestCase):
    def test_audio_provider_selection_preserves_legacy_and_supports_lists(self):
        source = (ROOT/"lidarr/Audio.service.bash").read_text(encoding="utf-8")
        body = re.search(r"verifyConfig \(\) \{(.*?)\n\}", source, re.S).group(1)
        body = body.replace("source /config/extended.conf", ":")
        for setting, expected in (("deezer", "true false"), ("tidal", "false true"),
                                  ("both", "true true"), ("all", "true true"),
                                  ("soulseek", "false false"), ("deezer,soulseek", "true false"),
                                  ("tidal,soulseek", "false true")):
            code = 'log(){ :; }; sleep(){ :; }; verifyConfig(){'+body+'\n}; enableAudio=true; enableSoulseek=true; dlClientSource="$1"; verifyConfig; printf "%s %s" "$dlClientUseDeezer" "$dlClientUseTidal"'
            result = subprocess.run([BASH, "-c", code, "test", setting], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected, setting)

    def test_disabled_or_unselected_soulseek_does_not_invoke_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            conf = Path(temporary)/"extended.conf"
            env = {**os.environ, "ARR_EXTENDED_CONF": str(conf), "ARR_SOULSEEK_APP": "lidarr", "ARR_SOULSEEK_WORKER": "/does-not-exist"}
            for settings in ('enableSoulseek="false"', 'enableSoulseek="true"\ndlClientSource="deezer"'):
                conf.write_text(settings, encoding="utf-8")
                result = subprocess.run([BASH, str(ROOT/"universal/Soulseek.bash")], env=env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_streaming_retry_schedule_is_not_passed_to_soulseek(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conf = root/"extended.conf"
            worker = root/"worker.py"
            conf.write_text('enableSoulseek="true"\ndlClientSource="all"\nfailedDownloadRetryDays="7,14,30"\nsoulseekFailedSearchDays="7"\n', encoding="utf-8")
            worker.write_text('import os\nprint(os.environ["SOULSEEK_FAILED_SEARCH_DAYS"])\n', encoding="utf-8")
            env = {**os.environ, "ARR_EXTENDED_CONF": str(conf), "ARR_SOULSEEK_APP": "lidarr",
                   "ARR_SOULSEEK_WORKER": str(worker), "ARR_CONFIG_DIR": str(root)}
            result = subprocess.run([BASH, str(ROOT/"universal/Soulseek.bash")], env=env,
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "7")

    def test_soulseek_waits_for_arr_before_starting_worker(self):
        source = (ROOT/"universal/Soulseek.bash").read_text(encoding="utf-8")
        self.assertIn("wait_for_arr_ready()", source)
        self.assertLess(source.index("wait_for_arr_ready\n"), source.index('python3 "$worker" "$@" 2>&1'))

    def test_autoconfig_allows_lidarr_task_manager_to_finish_starting(self):
        source = (ROOT/"lidarr/AutoConfig.service.bash").read_text(encoding="utf-8")
        ready = source.index("verifyApiAccess")
        grace = source.index('sleep "${arrStartupGraceSeconds:-3}"')
        write = source.index('if [ "$configureMediaManagement"')
        self.assertLess(ready, grace)
        self.assertLess(grace, write)

    def test_every_installer_downloads_worker_and_correct_service(self):
        for app in ("lidarr", "sonarr", "radarr"):
            source = (ROOT/app/"setup.bash").read_text(encoding="utf-8")
            self.assertIn("/universal/python/Soulseek.py", source)
            self.assertIn("/universal/Soulseek.bash", source)
            self.assertIn("/universal/Soulseek.service.bash", source)
            self.assertIn(f"s/__APP__/{app}/g", source)
            self.assertNotIn("RandomNinjaAtk/arr-scripts", source)


    def test_legacy_bootstraps_use_current_sources_and_report_download_failure(self):
        for app in ("readarr", "ra-rom-downloader"):
            with self.subTest(app=app), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                trace = root / "trace"
                def shell_path(path):
                    value = Path(path).as_posix()
                    return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" and value[1:2] == ":" else value
                environment = {**os.environ, "TRACE": shell_path(trace)}
                # A shell function shadows the real executable even if Bash resets PATH.
                wrapper = '\n'.join((
                    'curl() {',
                    ' printf "%s\\n" "$@" > "$TRACE"',
                    ' if [ "${FAKE_EXIT:-0}" != "0" ]; then return "$FAKE_EXIT"; fi',
                    ' printf "exit 0\\n"',
                    '}',
                    'export -f curl',
                    '[ "$(type -t curl)" = function ] || exit 99',
                    'source "$1"',
                ))
                for base, status in (("https://raw.githubusercontent.com/SickProdigy/arr-scripts/main", "0"),
                                     ("https://example.test/source", "0"), ("https://example.test/source", "22")):
                    environment.update(ARR_SCRIPTS_RAW_BASE=base, FAKE_EXIT=status)
                    result = subprocess.run([BASH, "-c", wrapper, "--", (ROOT/app/"scripts_init.bash").as_posix()], env=environment, capture_output=True, text=True)
                    self.assertEqual(result.returncode, int(status), result.stderr)
                    self.assertTrue(trace.exists(), repr((result.stdout, result.stderr)))
                    self.assertIn(base + "/" + app + "/setup.bash", trace.read_text())


if __name__ == "__main__":
    unittest.main()
