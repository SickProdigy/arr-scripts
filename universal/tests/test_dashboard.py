import importlib.util
import tempfile
import time
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
        self.assertEqual(tasks["musicbrainz-genres-submit"]["preview"], "musicbrainz-genres-preview")

    def test_configuration_masks_secrets_and_preserves_layout_on_save(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "extended.conf"
            config.write_text('enableDashboard="true" # enabled\ndashboardPassword=\'old$secret\' # hidden\n', encoding="utf-8")
            groups = DASHBOARD.configuration_fields(config)
            fields = {field["name"]: field for group in groups for field in group["fields"]}
            self.assertEqual(fields["dashboardPassword"]["value"], "")
            self.assertTrue(fields["dashboardPassword"]["configured"])
            backup = DASHBOARD.update_configuration({"enableDashboard": "false", "dashboardPassword": "new'password"}, config)
            self.assertTrue(backup.is_file())
            self.assertEqual(DASHBOARD.read_config(config)["dashboardPassword"], "new'password")
            self.assertIn("# enabled", config.read_text())

    def test_replaygain_schedule_uses_success_interval_or_hourly_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            old_state, old_dashboard, old_logs = DASHBOARD.REPLAYGAIN_STATE_DIR, DASHBOARD.STATE_DIR, DASHBOARD.LOG_DIR
            DASHBOARD.REPLAYGAIN_STATE_DIR = Path(directory) / "replaygain"
            DASHBOARD.STATE_DIR = Path(directory) / "dashboard"
            DASHBOARD.LOG_DIR = Path(directory) / "logs"
            DASHBOARD.REPLAYGAIN_STATE_DIR.mkdir()
            now = int(time.time())
            (DASHBOARD.REPLAYGAIN_STATE_DIR / "maintenance.last-attempt").write_text(str(now - 60))
            (DASHBOARD.REPLAYGAIN_STATE_DIR / "maintenance.last-success").write_text(str(now - 60))
            try:
                store = DASHBOARD.JobStore("lidarr", {"enableReplaygainTags": "true", "enableReplaygainMaintenance": "true", "replaygainMaintenanceDays": "7"})
                task = next(item for item in store.public_tasks() if item["id"] == "replaygain-maintenance")
                self.assertAlmostEqual(task["next_execution"], now - 60 + 7 * 86400, delta=1)
                (DASHBOARD.REPLAYGAIN_STATE_DIR / "maintenance.last-attempt").write_text(str(now))
                task = next(item for item in store.public_tasks() if item["id"] == "replaygain-maintenance")
                self.assertAlmostEqual(task["next_execution"], now + 3600, delta=1)
            finally:
                DASHBOARD.REPLAYGAIN_STATE_DIR, DASHBOARD.STATE_DIR, DASHBOARD.LOG_DIR = old_state, old_dashboard, old_logs

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
