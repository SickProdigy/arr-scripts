from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AudioServiceTests(unittest.TestCase):
    def test_busy_timeout_defers_instead_of_continuing(self):
        source = (ROOT / "Audio.service.bash").read_text(encoding="utf-8")

        self.assertNotIn("waiting for active tasks; continuing", source)
        self.assertIn("deferring audio work until the next cycle", source)
        self.assertGreaterEqual(source.count("if ! LidarrTaskStatusCheck; then"), 4)


if __name__ == "__main__":
    unittest.main()
