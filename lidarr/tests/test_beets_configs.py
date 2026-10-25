from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BeetsConfigurationTests(unittest.TestCase):
    def test_download_and_import_workflows_share_enrichment(self):
        download = (ROOT / "beets-config.yaml").read_text(encoding="utf-8")
        imported = (ROOT / "beets-config-lidarr.yaml").read_text(encoding="utf-8")
        download_plugins = next(line for line in download.splitlines() if line.startswith("plugins:"))
        imported_plugins = next(line for line in imported.splitlines() if line.startswith("plugins:"))

        for plugin in ("musicbrainz", "chroma", "embedart", "lastgenre", "deezer", "mbsync"):
            self.assertIn(plugin, download_plugins)
            self.assertIn(plugin, imported_plugins)
        for section in ("chroma:", "embedart:", "lastgenre:", "musicbrainz:", "deezer:"):
            self.assertIn("\n" + section + "\n", download)
            self.assertIn("\n" + section + "\n", imported)

        self.assertNotIn("lyrics", download_plugins)
        self.assertNotIn("lyrics", imported_plugins)
        self.assertIn("importadded", imported_plugins)
        self.assertIn("preserve_write_mtimes: yes", imported)


if __name__ == "__main__":
    unittest.main()
