import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("arr_dashboard", ROOT / "dashboard.py")
DASHBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DASHBOARD)


class DashboardTests(unittest.TestCase):
    def test_config_parser_ignores_comments_and_keeps_empty_values(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "extended.conf"
            config.write_text('enableDashboard="true" # comment\ndashboardPassword=""\nplain=value\n', encoding="utf-8")
            self.assertEqual(DASHBOARD.read_config(config), {
                "enableDashboard": "true", "dashboardPassword": "", "plain": "value"})

    def test_lidarr_catalog_has_preview_gates(self):
        tasks = DASHBOARD.task_catalog("lidarr", {"enableReplaygainTags": "true", "enableRatings": "true"})
        self.assertEqual(tasks["replaygain-backfill"]["preview"], "replaygain-preview")
        self.assertEqual(tasks["lyrics-backfill"]["preview"], "lyrics-preview")
        self.assertEqual(tasks["ratings-backfill"]["preview"], "ratings-preview")
        self.assertEqual(tasks["release-groups-apply"]["preview"], "release-groups-preview")

    def test_other_apps_start_without_unsafe_commands(self):
        self.assertEqual(DASHBOARD.task_catalog("sonarr", {}), {})
        self.assertEqual(DASHBOARD.DEFAULT_PORTS, {
            "lidarr": 8687, "sonarr": 8990, "radarr": 7879, "readarr": 8788})

    def test_log_catalog_only_returns_real_log_files(self):
        with tempfile.TemporaryDirectory() as directory:
            old = DASHBOARD.LOG_DIR
            DASHBOARD.LOG_DIR = Path(directory)
            try:
                (Path(directory) / "Audio-1.txt").write_text("ok")
                (Path(directory) / "secret.conf").write_text("no")
                self.assertEqual(DASHBOARD.available_logs(), {"Audio-1.txt": "Audio-1.txt"})
            finally:
                DASHBOARD.LOG_DIR = old


if __name__ == "__main__":
    unittest.main()
