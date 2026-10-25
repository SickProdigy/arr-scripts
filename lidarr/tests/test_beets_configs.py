from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BeetsConfigurationTests(unittest.TestCase):
    def test_only_post_import_beets_enrichment_remains(self):
        imported = (ROOT / "beets-config.yaml").read_text(encoding="utf-8")
        imported_plugins = next(line for line in imported.splitlines() if line.startswith("plugins:"))

        for plugin in ("musicbrainz", "chroma", "embedart", "lastgenre", "deezer", "mbsync"):
            self.assertIn(plugin, imported_plugins)
        for section in ("chroma:", "embedart:", "lastgenre:", "musicbrainz:", "deezer:"):
            self.assertIn("\n" + section + "\n", imported)

        self.assertIn("Full post-import enrichment", imported)
        self.assertNotIn("lyrics", imported_plugins)
        self.assertIn("importadded", imported_plugins)
        self.assertIn("preserve_write_mtimes: yes", imported)

    def test_audio_handoff_uses_exact_lidarr_ids_without_staging_beets(self):
        audio = (ROOT / "Audio.service.bash").read_text(encoding="utf-8")
        tagger = (ROOT / "BeetsTagger.bash").read_text(encoding="utf-8")
        setup = (ROOT / "setup.bash").read_text(encoding="utf-8")

        self.assertIn('TagWithLidarrMusicBrainzIds "$audioPath/incomplete"', audio)
        self.assertIn("MusicBrainz ID tagging failed; refusing Lidarr import", audio)
        self.assertNotIn("ProcessWithBeets", audio)
        self.assertNotIn("beet -P lyrics", audio)
        self.assertIn("-c /config/extended/beets-config.yaml", tagger)
        self.assertIn("/lidarr/beets-config.yaml", setup)

        for source in (audio, tagger, setup):
            self.assertNotIn("beets-config-lidarr.yaml", source)


if __name__ == "__main__":
    unittest.main()
