#!/usr/bin/env python3
"""Standardize album JPEG artwork; dry-run unless --apply is supplied."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


NAMES = ('cover.jpg', 'cover.jpeg', 'coverart.jpg', 'coverart.jpeg',
         'album.jpg', 'album.jpeg', 'folder.jpg', 'folder.jpeg')


def fingerprint(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('not a regular artwork file')
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        if stream.read(3) != b'\xff\xd8\xff':
            raise ValueError('not JPEG artwork; preserved')
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if signature(before) != signature(after):
        raise ValueError('artwork changed while reading; preserved')
    return digest.hexdigest(), signature(after)


def migrate(root, apply=False, emit=print):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('music root must be a directory')
    counts = dict(created=0, duplicates=0, preserved=0, errors=0)

    def report(action, path, **details):
        emit(json.dumps(dict(action=action, path=str(path), **details)))

    def walk_error(error):
        counts['errors'] += 1
        report('error', error.filename, reason=str(error))

    for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        directory = Path(current)
        dirs[:] = sorted(d for d in dirs if not (directory / d).is_symlink())
        # The root is /music, with artist/album below it. Never repurpose
        # artist-level folder artwork, artist.jpg, fanart or Kodi NFO files.
        if len(directory.relative_to(root).parts) < 2:
            continue
        if not directory.resolve().is_relative_to(root):
            continue
        candidates = sorted((directory / f for f in files if f.lower() in NAMES),
                            key=lambda p: (NAMES.index(p.name.lower()), p.name))
        if not candidates:
            continue
        cover = directory / 'cover.jpg'
        try:
            # A pre-existing cover always wins, including an invalid/symlink
            # cover: report it rather than replacing user artwork.
            source = cover if os.path.lexists(cover) else candidates[0]
            expected = fingerprint(source)
            if not os.path.lexists(cover):
                # Preserve existing PNG covers rather than silently selecting
                # a second, potentially different, primary cover.
                if any(f.lower() == 'cover.png' for f in files):
                    report('preserved', source, reason='cover.png already exists')
                    counts['preserved'] += 1
                    continue
                if apply:
                    fd, staged = tempfile.mkstemp(prefix='.arr-cover-', suffix='.jpg', dir=directory)
                    os.close(fd)
                    try:
                        shutil.copy2(source, staged)
                        if fingerprint(source) != expected or fingerprint(Path(staged))[0] != expected[0]:
                            raise ValueError('source changed during copy; preserved')
                        # Publish atomically without overwriting a concurrent cover.
                        os.link(staged, cover)
                    finally:
                        os.unlink(staged)
                report('created' if apply else 'would-create', cover, source=str(source))
                counts['created'] += 1
            target_hash = fingerprint(cover)[0] if apply else expected[0]
            for old in candidates:
                if old == cover:
                    continue
                try:
                    old_fingerprint = fingerprint(old)
                    if old_fingerprint[0] != target_hash:
                        counts['preserved'] += 1
                        report('preserved', old, reason='different image contents')
                        continue
                    if apply:
                        if fingerprint(cover)[0] != target_hash or fingerprint(old) != old_fingerprint:
                            raise ValueError('artwork changed before cleanup; preserved')
                        old.unlink()
                    report('removed-duplicate' if apply else 'would-remove-duplicate', old, kept=str(cover))
                    counts['duplicates'] += 1
                except (OSError, ValueError) as error:
                    counts['errors'] += 1
                    report('error', old, reason=str(error))
        except (OSError, ValueError) as error:
            counts['errors'] += 1
            report('error', directory, reason=str(error))
    emit(json.dumps(dict(summary=counts, dry_run=not apply)))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', default='/music', help='Music root containing artist/album directories')
    parser.add_argument('--apply', action='store_true', help='Create cover.jpg and delete byte-identical legacy copies')
    args = parser.parse_args()
    try:
        counts = migrate(args.path, args.apply)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
    return int(bool(counts['errors']))


if __name__ == '__main__':
    raise SystemExit(main())
