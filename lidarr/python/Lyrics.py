"""Durable, independent LRCMux lyrics queue. No Beets lyric fetching."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import xml.etree.ElementTree as ET

EXTENSIONS = {'.mp3', '.flac', '.m4a', '.mp4', '.opus', '.ogg', '.aac', '.wav', '.aiff', '.wma'}


def log(message):
    print('Lyrics: '+message, flush=True)


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def identity(meta):
    recording = meta.get('recording')
    fields = [recording, meta.get('release_track'), meta.get('disc'), meta.get('track'), round(meta['duration'])] if recording else [normalized(meta[k]) for k in ('artist', 'title', 'album')]+[round(meta['duration']), meta.get('disc'), meta.get('track')]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def metadata(path):
    from mediafile import MediaFile
    audio = MediaFile(str(path))
    return dict(artist=audio.artist or audio.albumartist or '', title=audio.title or '',
                album=audio.album or '', duration=float(audio.length or 0),
                recording=audio.mb_trackid or '', release_track=audio.mb_releasetrackid or '',
                disc=audio.disc, track=audio.track)


def meaningful(value):
    if isinstance(value, (list, tuple)):
        return any(meaningful(v) for v in value)
    if hasattr(value, 'text'):
        return meaningful(value.text)
    return bool(str(value or '').strip())


def embedded(path):
    import mutagen
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError('Unreadable audio')
    return any(meaningful(v) for k, v in (audio.tags or {}).items()
               if 'lyric' in str(k).casefold() or str(k).casefold().startswith(('uslt', 'sylt')) or str(k) == '\xa9lyr')


def local_lyrics(path):
    for suffix in ('.lrc', '.lyrics', '.txt'):
        sidecar = path.with_suffix(suffix)
        if sidecar.is_file() and not sidecar.is_symlink():
            if sidecar.stat().st_size > 1024*1024:
                continue
            text = sidecar.read_text(encoding='utf-8-sig').strip()
            if text:
                return text
    return ''


class Deferred(Exception):
    def __init__(self, reason, seconds=300):
        self.reason, self.seconds = reason, seconds


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def retry_after(value, now):
    try:
        seconds = int(value)
    except (ValueError, TypeError):
        try:
            seconds = int(parsedate_to_datetime(value).replace(tzinfo=timezone.utc).timestamp()-now)
        except (ValueError, TypeError, OverflowError):
            seconds = 300
    return max(1, seconds)


class Client:
    def __init__(self, endpoint='https://api.lrcmux.dev', timeout=15):
        parts = urlsplit(endpoint)
        if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username is not None or parts.query or parts.fragment:
            raise ValueError('Invalid LRCMux endpoint')
        self.endpoint, self.timeout = endpoint.rstrip('/'), timeout
        self.opener = build_opener(NoRedirect())

    def fetch(self, meta):
        query = {k: meta[k] for k in ('artist', 'title', 'album')}
        query.update(duration=round(meta['duration']), format='json', level='line')
        request = Request(self.endpoint+'/get?'+urlencode(query), headers={'User-Agent': 'arr-scripts-lyrics/1.0', 'Accept': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(1024*1024+1)
                if len(body) > 1024*1024:
                    raise Deferred('oversized response')
                result = json.loads(body)
        except HTTPError as error:
            status, delay = error.code, retry_after(error.headers.get('Retry-After'), time.time())
            error.close()
            if status == 404:
                return {'status': 'missing', 'text': ''}
            if status == 429:
                raise Deferred('rate limited', delay)
            if status >= 500:
                raise Deferred('provider unavailable')
            raise Deferred('HTTP '+str(status), 86400)
        except (URLError, TimeoutError, OSError, ValueError):
            raise Deferred('request failed') from None
        return parse_result(meta, result)


def parse_result(meta, result):
    track, info = result['track'], result['meta']
    if normalized(track['artist']) != normalized(meta['artist']) or normalized(track['title']) != normalized(meta['title']):
        raise Deferred('response identity mismatch', 86400)
    if track.get('album') and meta['album'] and normalized(track['album']) != normalized(meta['album']):
        raise Deferred('response album mismatch', 86400)
    if not track.get('duration') or abs(float(track['duration'])-meta['duration']) > 3:
        raise Deferred('response duration mismatch', 86400)
    if info.get('instrumental'):
        return {'status': 'instrumental', 'text': ''}
    timed = info['level'] in ('line', 'word')
    lines = []
    previous = -1
    for line in result['lines']:
        text = line['text'].strip()
        if not text:
            continue
        if timed:
            start = line['start']
            if not isinstance(start, int) or start < previous or start < 0:
                raise Deferred('invalid lyric timestamps', 86400)
            previous = start
            minutes, remainder = divmod(start, 60000)
            seconds, millis = divmod(remainder, 1000)
            text = f'[{minutes:02}:{seconds:02}.{millis:03}]'+text
        lines.append(text)
    return {'status': 'success' if lines else 'missing', 'text': '\n'.join(lines)}


class Queue:
    def __init__(self, state, clock=time.time):
        self.clock = clock
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state/'jobs.sqlite', timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL, next_run REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS cache (id TEXT PRIMARY KEY, result TEXT NOT NULL, expires REAL NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value REAL NOT NULL)')
        self.db.commit()

    def enqueue(self, directory, album_id='', read=metadata, has=embedded):
        directory = Path(directory).resolve()
        count = 0
        for path in directory.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in EXTENSIONS or path.is_symlink() or not path.resolve().is_relative_to(directory):
                continue
            try:
                if has(path):
                    continue
                meta = read(path)
                if not meta['artist'] or not meta['title'] or meta['duration'] <= 0:
                    continue
                # Key includes source path to preserve distinct copies, plus identity
                # so a replacement at the same path never inherits the old lyrics.
                ident = hashlib.sha256((str(path)+'\0'+identity(meta)).encode()).hexdigest()
                data = dict(path=str(path), meta=meta, album_id=str(album_id), local=local_lyrics(path))
                with self.db:
                    self.db.execute("INSERT INTO jobs(id,data,state,next_run) VALUES(?,?,'pending',?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,state=CASE WHEN jobs.state='done' THEN 'pending' ELSE jobs.state END", (ident, json.dumps(data), self.clock()+30))
                count += 1
            except Exception as error:
                log('local enqueue skipped '+type(error).__name__)
        log(f'enqueued {count} tracks; imports continue')
        return count

    def save(self, ident, state, delay=0, attempt=False):
        with self.db:
            self.db.execute('UPDATE jobs SET state=?,next_run=?,attempts=attempts+? WHERE id=?', (state, self.clock()+delay, int(attempt), ident))

    def work(self, client, resolve, read=metadata, has=embedded, write=None, budget=180):
        start = time.monotonic()
        paused = self.db.execute("SELECT value FROM settings WHERE name='rate_limit'").fetchone()
        if paused and paused[0] > self.clock():
            return
        for row in self.db.execute("SELECT * FROM jobs WHERE state IN ('pending','retry') AND next_run<=? ORDER BY next_run LIMIT 10", (self.clock(),)).fetchall():
            if time.monotonic()-start >= budget:
                break
            data = json.loads(row['data'])
            ident = row['id']
            try:
                path = resolve(data)
                if identity(read(path)) != identity(data['meta']):
                    raise Deferred('file identity changed', 86400)
                if has(path):
                    self.save(ident, 'done')
                    log('preserved existing lyrics')
                    continue
                text = local_lyrics(path) or data.get('local', '')
                if not text:
                    key = hashlib.sha256((client.endpoint+'\0'+identity(data['meta'])).encode()).hexdigest()
                    cached = self.db.execute('SELECT result FROM cache WHERE id=? AND expires>?', (key, self.clock())).fetchone()
                    result = json.loads(cached[0]) if cached else client.fetch(data['meta'])
                    if not cached:
                        ttl = 30*86400 if result['status'] != 'missing' else 86400
                        with self.db:
                            self.db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)', (key, json.dumps(result), self.clock()+ttl))
                    if result['status'] in ('missing', 'instrumental'):
                        self.save(ident, 'retry', 86400 if result['status'] == 'missing' else 30*86400)
                        log(result['status']+'; cached')
                        continue
                    text = result['text']
                # The HTTP request may outlive an import, retag, or manual lyric edit.
                latest = resolve(data)
                if latest != path or identity(read(path)) != identity(data['meta']):
                    raise Deferred('file moved or identity changed')
                if has(path):
                    self.save(ident, 'done')
                    log('preserved concurrent lyrics')
                    continue
                (write or embed_safe)(path, text, data['meta'])
                self.save(ident, 'done')
                log('embedded lyrics')
            except Deferred as error:
                delay = max(error.seconds, min(86400, 60*2**min(row['attempts'], 10)))
                review = row['attempts'] >= 7 and error.reason not in ('rate limited', 'waiting for final library path', 'waiting for unambiguous imported file')
                self.save(ident, 'review' if review else 'retry', delay, error.reason != 'rate limited')
                log(error.reason+('; review required' if review else '; deferred'))
                if error.reason == 'rate limited':
                    with self.db:
                        self.db.execute("INSERT OR REPLACE INTO settings VALUES('rate_limit',?)", (self.clock()+delay,))
                    break
            except Exception as error:
                self.save(ident, 'review' if row['attempts'] >= 7 else 'retry', 300, True)
                log(type(error).__name__+'; deferred')


def stamp(path):
    stat = path.stat()
    # ReplayGain can preserve mtime, and same-length tag edits can preserve size.
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            digest.update(block)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, digest.digest()


def embed_safe(path, text, expected):
    from mediafile import MediaFile, FileTypeError
    original = stamp(path)
    if identity(metadata(path)) != identity(expected) or embedded(path):
        raise Deferred('file changed before write')
    # Stage the tag write; failures or interruption never damage the original.
    fd, temporary = tempfile.mkstemp(prefix='.arr-lyrics-', suffix=path.suffix, dir=path.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        shutil.copy2(path, temporary)
        try:
            audio = MediaFile(str(temporary))
            audio.lyrics = text
            audio.save()
        except FileTypeError:
            # Only unsupported embedding falls back; do not hide other write errors.
            sidecar = path.with_suffix('.lrc' if re.search(r'^\[\d+:\d+', text, re.M) else '.lyrics')
            if stamp(path) != original or identity(metadata(path)) != identity(expected):
                raise Deferred('file changed during write')
            with sidecar.open('x', encoding='utf-8') as handle:
                handle.write(text+'\n')
            return
        if stamp(path) != original or embedded(path) or identity(metadata(path)) != identity(expected):
            raise Deferred('file changed during write')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Resolver:
    def __init__(self, config, roots):
        self.roots = [Path(p).resolve() for p in roots]
        root = ET.parse(Path(config)/'config.xml').getroot()
        self.url = os.environ.get('LYRICS_ARR_URL') or f"http://127.0.0.1:{root.findtext('Port')}/{(root.findtext('UrlBase') or '').strip('/')}"
        self.key = os.environ.get('LYRICS_ARR_KEY') or root.findtext('ApiKey')
        self.opener = build_opener(NoRedirect())
        self.albums = {}

    def safe(self, path):
        path = Path(path)
        return not path.is_symlink() and path.is_file() and any(path.resolve().is_relative_to(root) for root in self.roots)

    def __call__(self, data):
        original = Path(data['path'])
        if self.safe(original):
            if identity(metadata(original)) != identity(data['meta']):
                raise Deferred('original path now has different identity', 86400)
            return original
        album = data.get('album_id')
        if not album:
            raise Deferred('waiting for final library path')
        if album not in self.albums:
            request = Request(self.url.rstrip('/')+'/api/v1/trackfile?'+urlencode({'albumId': album}), headers={'X-Api-Key': self.key})
            try:
                with self.opener.open(request, timeout=10) as response:
                    self.albums[album] = json.load(response)
            except HTTPError as error:
                error.close()
                raise Deferred('Arr path lookup failed') from None
            except (URLError, OSError, ValueError):
                raise Deferred('Arr path lookup failed') from None
        matches = []
        for file in self.albums[album]:
            path = Path(file['path'])
            if self.safe(path) and identity(metadata(path)) == identity(data['meta']):
                matches.append(path)
        if len(matches) != 1:
            raise Deferred('waiting for unambiguous imported file')
        return matches[0]


@contextmanager
def lock(state):
    import fcntl
    Path(state).mkdir(parents=True, exist_ok=True)
    with (Path(state)/'worker.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enqueue', type=Path)
    parser.add_argument('--album-id', default='')
    parser.add_argument('--work', action='store_true')
    parser.add_argument('--retry', help='Reset one reviewed job ID after fixing its cause')
    parser.add_argument('--status', action='store_true', help='Show queue states and job IDs without network requests')
    args = parser.parse_args()
    config = os.environ.get('LYRICS_CONFIG', '/config')
    state = Path(config)/'extended/lyrics'
    if args.status:
        queue = Queue(state)
        try:
            for row in queue.db.execute('SELECT id,state,next_run,attempts FROM jobs ORDER BY next_run'):
                log(f"job={row['id']} state={row['state']} next_run={row['next_run']:.0f} attempts={row['attempts']}")
        finally:
            queue.db.close()
    elif args.retry:
        queue = Queue(state)
        try:
            with queue.db:
                queue.db.execute("UPDATE jobs SET state='pending',attempts=0,next_run=0 WHERE id=? AND state='review'", (args.retry,))
        finally:
            queue.db.close()
    elif args.enqueue:
        queue = Queue(state)
        try:
            queue.enqueue(args.enqueue, args.album_id)
        finally:
            queue.db.close()
    elif args.work:
        with lock(state) as acquired:
            if not acquired:
                return
            queue = Queue(state)
            try:
                client = Client(os.environ.get('LYRICS_URL', 'https://api.lrcmux.dev'))
                resolver = Resolver(config, os.environ.get('LYRICS_ROOTS', '/music').split(os.pathsep))
                queue.work(client, resolver)
            finally:
                queue.db.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        log(type(error).__name__+'; helper failed, imports continue')
        raise SystemExit(1)
