import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    'artwork_migration', Path(__file__).resolve().parents[1] / 'python/ArtworkMigration.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class ArtworkMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.album = self.root / 'Artist' / 'Album'
        self.album.mkdir(parents=True)
        self.image = b'\xff\xd8\xffsame artwork\xff\xd9'

    def write(self, name, data=None):
        path = self.album / name
        path.write_bytes(self.image if data is None else data)
        return path

    def run_migration(self, apply=True):
        return migration.migrate(self.root, apply, emit=lambda _: None)

    def test_dry_run_does_not_write_or_delete(self):
        self.write('folder.jpg')
        self.write('coverart.jpg')
        result = self.run_migration(False)
        self.assertEqual(result['duplicates'], 2)
        self.assertEqual(sorted(p.name for p in self.album.iterdir()), ['coverart.jpg', 'folder.jpg'])

    def test_migrates_and_removes_only_identical_copies(self):
        self.write('coverart.jpg')
        self.write('folder.jpg')
        self.write('album.jpg', b'\xff\xd8\xffdifferent image\xff\xd9')
        self.write('artist.jpg')
        self.write('album.nfo', b'original metadata')
        result = self.run_migration()
        self.assertEqual(result['duplicates'], 2)
        self.assertEqual((self.album / 'cover.jpg').read_bytes(), self.image)
        self.assertEqual(sorted(p.name for p in self.album.iterdir()),
                         ['album.jpg', 'album.nfo', 'artist.jpg', 'cover.jpg'])
        self.assertEqual(self.run_migration()['created'], 0)

    def test_existing_cover_wins(self):
        cover = self.write('cover.jpg', b'\xff\xd8\xffchosen cover\xff\xd9')
        old = self.write('folder.jpg')
        self.run_migration()
        self.assertTrue(old.exists())
        self.assertEqual(cover.read_bytes(), b'\xff\xd8\xffchosen cover\xff\xd9')

    def test_keeps_artist_level_art_and_audio(self):
        artist = self.root / 'Artist' / 'folder.jpg'
        artist.write_bytes(self.image)
        audio = self.write('track.flac', b'original audio')
        self.write('folder.jpeg')
        self.run_migration()
        self.assertTrue(artist.exists())
        self.assertEqual(audio.read_bytes(), b'original audio')

    def test_non_jpeg_is_preserved(self):
        old = self.write('folder.jpg', b'not a JPEG')
        self.assertEqual(self.run_migration()['errors'], 1)
        self.assertTrue(old.exists())
        self.assertFalse((self.album / 'cover.jpg').exists())

    def test_existing_png_cover_is_preserved(self):
        self.write('cover.png', b'original png')
        self.write('folder.jpg')
        self.run_migration()
        self.assertFalse((self.album / 'cover.jpg').exists())
        self.assertTrue((self.album / 'folder.jpg').exists())

    def test_symlink_is_not_deleted(self):
        self.write('cover.jpg')
        link = self.album / 'folder.jpg'
        try:
            link.symlink_to(self.album / 'cover.jpg')
        except OSError:
            self.skipTest('symlinks unavailable')
        self.run_migration()
        self.assertTrue(link.is_symlink())


if __name__ == '__main__':
    unittest.main()
