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

    def test_every_installer_downloads_worker_and_correct_service(self):
        for app in ("lidarr", "sonarr", "radarr"):
            source = (ROOT/app/"setup.bash").read_text(encoding="utf-8")
            self.assertIn("/universal/python/Soulseek.py", source)
            self.assertIn("/universal/Soulseek.bash", source)
            self.assertIn("/universal/Soulseek.service.bash", source)
            self.assertIn(f"s/__APP__/{app}/g", source)
            self.assertNotIn("RandomNinjaAtk/arr-scripts", source)


if __name__ == "__main__":
    unittest.main()
