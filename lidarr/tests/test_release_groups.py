import importlib.util
import io
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "release_groups", Path(__file__).parents[1] / "python" / "ReleaseGroups.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeLidarr:
    def __init__(self, files):
        self.files = files
        self.puts = []

    def call(self, path, data=None, method=None, **query):
        if path == "trackfile":
            return self.files
        if method == "PUT":
            self.puts.append((path, data))
            return data
        raise AssertionError((path, method, query))


class ReleaseGroupTests(unittest.TestCase):
    def test_preview_repairs_only_recognized_blank_suffixes(self):
        client = FakeLidarr([
            {"id": 1, "path": "/music/a-[900 kbps]-Tidal.flac"},
            {"id": 2, "path": "/music/b-[900 kbps]-Other.flac"},
            {"id": 3, "path": "/music/c-[900 kbps]-Deezer.flac", "releaseGroup": "Original"},
        ])
        output = io.StringIO()
        summary = MODULE.run(client, album_id=1, stream=output)
        self.assertEqual(summary, {"selected": 2, "updated": 0, "already_set": 1, "apply": False})
        self.assertIn('"releaseGroup": "Tidal"', output.getvalue())
        self.assertEqual(client.puts, [])

    def test_apply_updates_blank_record_without_renaming(self):
        client = FakeLidarr([{"id": 4, "path": "/music/a-[320 kbps]-Deemix.mp3"}])
        summary = MODULE.run(client, apply=True, album_id=1, stream=io.StringIO())
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(client.puts[0][0], "trackfile/4")
        self.assertEqual(client.puts[0][1]["releaseGroup"], "Deemix")


if __name__ == "__main__":
    unittest.main()
