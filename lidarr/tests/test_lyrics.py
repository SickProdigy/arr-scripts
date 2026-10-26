import importlib.util
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from test_musicbrainz_genres_wrapper import BASH, ROOT, shell_path

spec = importlib.util.spec_from_file_location('independent_lyrics', ROOT/'python/Lyrics.py')
lyrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lyrics)

META = dict(artist='Beyoncé', title='Song', album='Album', duration=120.0,
            recording='recording-id', release_track='release-track', disc=1, track=1)


class MaintenanceTests(unittest.TestCase):
    def test_audit_counts_missing_complete_and_failed_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('missing.flac', 'complete.mp3', 'broken.ogg'):
                (root/name).write_bytes(b'fixture')
            def has(path):
                if path.name == 'broken.ogg':
                    raise OSError('bad file')
                return path.name == 'complete.mp3'
            self.assertEqual(lyrics.audit(root, has), {
                'scanned': 3, 'missing': 1, 'complete': 1, 'failed': 1})

    def test_successful_scan_waits_thirty_days_and_failures_retry_after_an_hour(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state = root/'state'; state.mkdir()
            queue = Mock(); queue.enqueue.return_value = 3
            with patch.object(lyrics, 'lock', return_value=nullcontext(True)), patch.object(lyrics, 'Queue', return_value=queue):
                lyrics.maintenance(state, root, clock=lambda: 100000)
                queue.enqueue.assert_called_once_with(root, strict=True)
                queue.db.close.assert_called_once()
                lyrics.maintenance(state, root, clock=lambda: 100001)
                self.assertEqual(queue.enqueue.call_count, 1)
                queue.enqueue.side_effect = RuntimeError('unreadable file')
                with self.assertRaises(RuntimeError):
                    lyrics.maintenance(state, root, clock=lambda: 2700000)
                record = json.loads((state/'maintenance.json').read_text())
                self.assertEqual(record['status'], 'failed')
                self.assertIn('elapsed_seconds', record)
                lyrics.maintenance(state, root, clock=lambda: 2703599)
                self.assertEqual(queue.enqueue.call_count, 2)
                queue.enqueue.side_effect = None
                lyrics.maintenance(state, root, clock=lambda: 2703600)
                self.assertEqual(queue.enqueue.call_count, 3)

    def test_invalid_root_interval_and_active_scan_do_not_enqueue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(lyrics, 'Queue') as queue:
                with self.assertRaises(ValueError):
                    lyrics.maintenance(root, root, days=0)
                with self.assertRaises(ValueError):
                    lyrics.maintenance(root, root/'absent')
                with patch.object(lyrics, 'lock', return_value=nullcontext(False)):
                    lyrics.maintenance(root, root)
                queue.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Linux service locking')
    def test_scanning_does_not_lock_out_lyrics_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            with lyrics.lock(directory) as worker:
                with lyrics.lock(directory, 'maintenance.lock') as scanner:
                    self.assertTrue(worker); self.assertTrue(scanner)
                    with lyrics.lock(directory, 'maintenance.lock') as other:
                        self.assertFalse(other)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.album = self.root/'Beyoncé'/'Album'/'CD 1'
        self.album.mkdir(parents=True)
        self.file = self.album/'01 Song.flac'
        self.file.write_bytes(b'fixture')
        self.now = 1000
        self.q = lyrics.Queue(self.root/'queue', lambda: self.now)
        self.meta = META.copy()
        self.read = lambda path: self.meta.copy()
        self.has = Mock(return_value=False)
        self.resolve = lambda data: self.file
        self.client = SimpleNamespace(endpoint='https://example.test', fetch=Mock(return_value={'status': 'success', 'text': '[00:01.000]Words'}))
        self.write = Mock()

    def tearDown(self):
        self.q.db.close()
        self.temp.cleanup()

    def enqueue(self):
        return self.q.enqueue(self.album.parent.parent, '12', self.read, self.has)

    def work(self):
        self.now += 301
        self.q.work(self.client, self.resolve, self.read, self.has, self.write)

    def state(self):
        return self.q.db.execute('select state from jobs').fetchone()[0]

    def test_enqueue_is_local_multidisc_unicode_and_coalesces_restarts(self):
        self.enqueue()
        self.enqueue()
        self.client.fetch.assert_not_called()
        self.assertEqual(self.q.db.execute('select count(*) from jobs').fetchone()[0], 1)
        self.q.db.close()
        self.q = lyrics.Queue(self.root/'queue', lambda: self.now)
        self.work()
        self.assertEqual(self.state(), 'done')
        self.write.assert_called_once()

    def test_existing_lyrics_never_requested(self):
        self.has.return_value = True
        self.assertEqual(self.enqueue(), 0)

    def test_strict_maintenance_enqueue_reports_partial_errors(self):
        broken = Mock(side_effect=OSError('unreadable file'))
        with self.assertRaisesRegex(RuntimeError, 'enqueue incomplete'):
            self.q.enqueue(self.album, '12', broken, self.has, strict=True)
        self.assertEqual(self.q.db.execute('select count(*) from jobs').fetchone()[0], 0)
        self.has.return_value = False
        self.enqueue()
        self.has.return_value = True
        self.work()
        self.client.fetch.assert_not_called()
        self.write.assert_not_called()

    def test_existing_local_sidecar_reused_after_move_and_preserved(self):
        sidecar = self.file.with_suffix('.lrc')
        sidecar.write_text('[00:01.000]Local words', encoding='utf-8')
        self.enqueue()
        moved = self.root/'final.flac'
        self.file.rename(moved)
        self.file = moved
        self.work()
        self.client.fetch.assert_not_called()
        self.assertEqual(self.write.call_args.args[1], '[00:01.000]Local words')
        self.assertTrue(sidecar.exists())

    def test_misses_and_instrumentals_are_distinct_and_cached(self):
        for status in ('missing', 'instrumental'):
            self.client.fetch.reset_mock()
            self.client.fetch.return_value = {'status': status, 'text': ''}
            self.q.db.execute('delete from cache')
            self.q.db.execute('delete from jobs')
            self.q.db.commit()
            self.enqueue()
            self.work()
            self.assertEqual(self.state(), 'retry')
            self.assertEqual(self.q.db.execute('select count(*) from cache').fetchone()[0], 1)
            self.now += 100
            self.work()
            self.client.fetch.assert_called_once()
            self.write.assert_not_called()

    def test_rate_limit_is_global_persistent_and_respects_retry_after(self):
        self.enqueue()
        self.client.fetch.side_effect = lyrics.Deferred('rate limited', 3600)
        self.work()
        next_run = self.q.db.execute('select next_run from jobs').fetchone()[0]
        self.assertGreaterEqual(next_run, self.now+3600)
        self.q.db.close()
        self.q = lyrics.Queue(self.root/'queue', lambda: self.now)
        self.work()
        self.client.fetch.assert_called_once()

    def test_changed_recording_never_written(self):
        self.enqueue()
        self.meta['recording'] = 'different'
        self.work()
        self.client.fetch.assert_not_called()
        self.write.assert_not_called()
        self.assertEqual(self.state(), 'retry')

    def test_concurrent_manual_lyrics_preserved(self):
        self.enqueue()
        self.has.side_effect = [False, True]
        self.work()
        self.client.fetch.assert_called_once()
        self.write.assert_not_called()

    def test_move_during_fetch_reuses_cached_success_next_attempt(self):
        self.enqueue()
        paths = [self.file, self.root/'new.flac']
        self.resolve = Mock(side_effect=paths)
        self.work()
        self.write.assert_not_called()
        self.resolve = lambda data: paths[1]
        self.work()
        self.client.fetch.assert_called_once()
        self.write.assert_called_once()

    def test_partial_progress_survives_failed_provider(self):
        (self.album/'02 Other.flac').write_bytes(b'fixture')
        self.enqueue()
        self.client.fetch.side_effect = [{'status': 'success', 'text': 'words'}, lyrics.Deferred('provider unavailable')]
        # Distinct recordings use distinct cache keys.
        for row in self.q.db.execute('select id,data from jobs').fetchall():
            data = json.loads(row['data'])
            if '02 Other' in data['path']:
                data['meta']['recording'] = 'other'
                self.q.db.execute('update jobs set data=? where id=?', (json.dumps(data), row['id']))
        self.q.db.commit()
        self.read = lambda path: {**META, 'recording': 'other' if '02 Other' in str(path) else META['recording']}
        self.resolve = lambda data: Path(data['path'])
        self.work()
        self.assertEqual(dict(self.q.db.execute('select state,count(*) from jobs group by state')), {'done': 1, 'retry': 1})

    def test_repeated_failures_require_review_instead_of_retrying_forever(self):
        self.enqueue()
        self.client.fetch.side_effect = lyrics.Deferred('provider unavailable')
        for _ in range(8):
            self.now += 86401
            self.work()
        self.assertEqual(self.state(), 'review')
        self.now += 86401
        self.work()
        self.assertEqual(self.client.fetch.call_count, 8)


class ContractTests(unittest.TestCase):
    def test_file_stamp_detects_same_size_edits_with_preserved_mtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'fixture'
            path.write_bytes(b'AAAA')
            stat = path.stat()
            before = lyrics.stamp(path)
            path.write_bytes(b'BBBB')
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertNotEqual(before, lyrics.stamp(path))

    def payload(self, instrumental=False):
        return {'track': META.copy(), 'meta': {'level': 'line', 'instrumental': instrumental},
                'lines': [{'start': 1000, 'text': 'Unicode ♫ words'}, {'start': 62001, 'text': 'Next'}]}

    def test_structured_lrcmux_contract_preserves_timestamps_and_plain(self):
        value = lyrics.parse_result(META, self.payload())
        self.assertEqual(value['text'], '[00:01.000]Unicode ♫ words\n[01:02.001]Next')
        p = self.payload()
        p['meta']['level'] = 'none'
        self.assertEqual(lyrics.parse_result(META, p)['text'], 'Unicode ♫ words\nNext')
        self.assertEqual(lyrics.parse_result(META, self.payload(True))['status'], 'instrumental')

    def test_wrong_track_and_duration_are_rejected(self):
        for key, value in (('title', 'Different'), ('artist', 'Other'), ('album', 'Other'), ('duration', 200)):
            p = self.payload()
            p['track'][key] = value
            with self.assertRaises(lyrics.Deferred):
                lyrics.parse_result(META, p)

    def test_http429_timeout_404_and_encoded_direct_query(self):
        client = lyrics.Client('https://example.test')
        for error, reason in ((HTTPError('url', 429, '', {'Retry-After': '900'}, None), 'rate limited'), (TimeoutError(), 'request failed')):
            client.opener = Mock()
            client.opener.open.side_effect = error
            with self.assertRaises(lyrics.Deferred) as caught:
                client.fetch(META)
            self.assertEqual(caught.exception.reason, reason)
            request = client.opener.open.call_args.args[0]
            self.assertIn('/get?', request.full_url)
            self.assertIn('Beyonc%C3%A9', request.full_url)
            self.assertIn('format=json', request.full_url)
        client.opener.open.side_effect = HTTPError('url', 404, '', {}, None)
        self.assertEqual(client.fetch(META)['status'], 'missing')
        self.assertEqual(lyrics.retry_after('900', 0), 900)
        self.assertEqual(lyrics.retry_after('Thu, 01 Jan 1970 00:15:00 GMT', 0), 900)

    def test_resolver_uses_current_lidarr_file_and_rejects_ambiguity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'config.xml').write_text('<Config><Port>8686</Port><ApiKey>fake</ApiKey></Config>')
            file = root/'library'/'Song.flac'
            file.parent.mkdir()
            file.write_bytes(b'fixture')
            resolver = lyrics.Resolver(root, [file.parent])
            resolver.albums['12'] = [{'path': str(file)}]
            with patch.object(lyrics, 'metadata', return_value=META):
                data = {'path': str(root/'missing'), 'album_id': '12', 'meta': META}
                self.assertEqual(resolver(data), file)
                resolver.albums['12'].append({'path': str(file)})
                with self.assertRaises(lyrics.Deferred):
                    resolver(data)


class HookTests(unittest.TestCase):
    def test_installer_wires_background_service_and_legacy_compatibility(self):
        source = (ROOT/'setup.bash').read_text()
        for name in ('Lyrics.bash', 'Lyrics.service.bash', 'python/Lyrics.py', 'BeetsLyrics.bash'):
            self.assertIn('/lidarr/'+name, source)
        self.assertIn('chmod 2775 /config/extended/lyrics', source)
        hook = (ROOT/'BeetsTagger.bash').read_text()
        self.assertIn('beet -P lyrics', hook)
        self.assertIn('bash /config/extended/Lyrics.bash', hook)
        self.assertNotIn('bash /config/extended/BeetsLyrics.bash', hook)
        audio = (ROOT/'Audio.service.bash').read_text()
        self.assertNotIn('beet -P lyrics', audio)
        self.assertNotIn('bash /config/extended/Lyrics.bash', audio)

    def test_metadata_failure_skips_enqueue_and_enqueue_failure_does_not_stop_import(self):
        source = (ROOT/'BeetsTagger.bash').read_text()
        start = source.index('\tBEETSDIR=/config/extended beet ')
        fragment = source[start:source.index('\t# Fix tags', start)]
        self.assertIn('-P lyrics', fragment)
        for status in (0, 1):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root/'logs').mkdir()
                (root/'extended').mkdir()
                (root/'extended/Lyrics.bash').write_text('echo QUEUED; exit 1\n')
                binary = root/'beet'
                binary.write_text(f'#!/usr/bin/env bash\nexit {status}\n')
                env = {**os.environ, 'PATH': shell_path(root)+':/usr/bin:/bin'}
                subprocess.run([BASH, '-c', 'chmod +x "$1"', '--', shell_path(binary)], env=env, check=True)
                script = root/'run.bash'
                script.write_text('logFileName=test.log\n'+fragment.replace('/config/', shell_path(root)+'/')+'\necho IMPORT_CONTINUES\n')
                result = subprocess.run([BASH, str(script), 'album'], env=env, capture_output=True, text=True, check=True)
                self.assertIn('IMPORT_CONTINUES', result.stdout)
                self.assertEqual('QUEUED' in result.stdout, status == 0)

    def test_disabled_and_legacy_disabled_modes_do_not_invoke_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            conf = Path(temporary)/'extended.conf'
            env = {**os.environ, 'ARR_EXTENDED_CONF': shell_path(conf), 'ARR_LYRICS_WORKER': '/missing'}
            for text in ('enableLyrics="false"', 'enableBeetsLyrics="false"', 'enableLyrics="false"\nenableBeetsLyrics="true"'):
                conf.write_text(text)
                result = subprocess.run([BASH, str(ROOT/'Lyrics.bash'), '--work'], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout+result.stderr, '')

    def test_hung_background_process_is_bounded_and_nonfatal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conf = root/'extended.conf'
            conf.write_text('enableLyrics="true"\nlyricsTimeout="1s"\n')
            binary = root/'python3'
            binary.write_text('#!/usr/bin/env bash\nsleep 30\n')
            env = {**os.environ, 'ARR_EXTENDED_CONF': shell_path(conf), 'PATH': shell_path(root)+':/usr/bin:/bin'}
            subprocess.run([BASH, '-c', 'chmod +x "$1"', '--', shell_path(binary)], env=env, check=True)
            started = time.monotonic()
            result = subprocess.run([BASH, str(ROOT/'Lyrics.bash'), '--work'], env=env, capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0)
            self.assertIn('queued work retained', result.stdout)
            self.assertLess(time.monotonic()-started, 8)


try:
    import mediafile
    import imageio_ffmpeg
    REAL_AUDIO = True
except ImportError:
    REAL_AUDIO = False


@unittest.skipUnless(REAL_AUDIO, 'mediafile and imageio-ffmpeg required for real audio fixtures')
class RealAudioTests(unittest.TestCase):
    def test_supported_formats_embed_synced_text_and_preserve_existing_tags(self):
        codecs = {'.mp3': 'libmp3lame', '.flac': 'flac', '.m4a': 'aac', '.opus': 'libopus'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for extension, codec in codecs.items():
                with self.subTest(extension=extension):
                    path = root/('Song'+extension)
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '1', '-c:a', codec, str(path)], check=True)
                    audio = mediafile.MediaFile(str(path))
                    audio.artist, audio.title, audio.album = 'Artist', 'Song', 'Album'
                    audio.save()
                    expected = lyrics.metadata(path)
                    lyrics.embed_safe(path, '[00:00.100]Words', expected)
                    self.assertTrue(lyrics.embedded(path))
                    self.assertEqual(mediafile.MediaFile(str(path)).lyrics, '[00:00.100]Words')
                    with self.assertRaises(lyrics.Deferred):
                        lyrics.embed_safe(path, 'overwrite', expected)
                    self.assertEqual(mediafile.MediaFile(str(path)).artist, 'Artist')

    def test_sylt_only_is_preserved_without_fetching(self):
        from mutagen.id3 import ID3, SYLT
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'Song.mp3'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc', '-t', '1', '-c:a', 'libmp3lame', str(path)], check=True)
            tags = ID3(path)
            tags.add(SYLT(encoding=3, lang='eng', format=2, type=1, text=[('Words', 100)]))
            tags.save(path)
            self.assertTrue(lyrics.embedded(path))

    def test_concurrent_file_change_aborts_staged_write_without_damaging_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'Song.flac'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc', '-t', '1', '-c:a', 'flac', str(path)], check=True)
            audio = mediafile.MediaFile(str(path))
            audio.artist, audio.title, audio.album = 'Artist', 'Song', 'Album'
            audio.save()
            expected = lyrics.metadata(path)
            original_save = mediafile.MediaFile.save
            def save_and_change(instance, *args, **kwargs):
                original_save(instance, *args, **kwargs)
                path.write_bytes(path.read_bytes()+b'concurrent edit')
            with patch.object(mediafile.MediaFile, 'save', save_and_change), self.assertRaises(lyrics.Deferred):
                lyrics.embed_safe(path, 'words', expected)
            self.assertFalse(lyrics.embedded(path))
            self.assertTrue(path.read_bytes().endswith(b'concurrent edit'))
            self.assertFalse(list(path.parent.glob('.arr-lyrics-*')))


if __name__ == '__main__':
    unittest.main()
