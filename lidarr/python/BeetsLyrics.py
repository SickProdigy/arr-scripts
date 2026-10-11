"""Fetch only missing lyrics from imported Beets items, embedding each success."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def meaningful(value):
    if isinstance(value, (list, tuple)):
        return any(meaningful(entry) for entry in value)
    if hasattr(value, "text"):
        return meaningful(value.text)
    return bool(str(value or "").strip())


def embedded_lyrics(path, read_tags):
    audio = read_tags(path)
    if audio is None:
        raise ValueError("Unsupported or unreadable audio")
    for key, value in (audio.tags or {}).items():
        key = str(key).casefold()
        if ("lyric" in key or key.startswith(("uslt", "sylt")) or key == "\xa9lyr") and meaningful(value):
            return True
    return False


def process(items, directory, config, library, read_tags, embed, run=subprocess.run):
    directory = Path(directory).resolve()
    fetched = skipped = failures = 0
    for item in items:
        path = Path(os.fsdecode(item.path)).resolve()
        if not path.is_relative_to(directory):
            continue
        try:
            if embedded_lyrics(path, read_tags):
                skipped += 1
                continue
            # Database lyrics can survive an interrupted file write. Embed them locally.
            text = str(item.get("lyrics", "") or "").strip()
            if not text:
                for suffix in (".lrc", ".lyrics", ".txt"):
                    sidecar = path.with_suffix(suffix)
                    if sidecar.is_file():
                        text = sidecar.read_text(encoding="utf-8-sig").strip()
                        if text:
                            break
            if text:
                embed(path, text)
                skipped += 1
                print(f"Beets lyrics: embedded existing local lyrics: {path.name}", flush=True)
                continue
            if item.get("lyrics_instrumental", False):
                skipped += 1
                continue
            print(f"Beets lyrics: fetching missing lyrics: {path.name}", flush=True)
            result = run(["beet", "-c", str(config), "-p", "lyrics", "-l", str(library),
                          "lyrics", f"id:{item.id}"], check=False)
            if result.returncode:
                failures += 1
                print(f"Beets lyrics: provider phase failed (exit {result.returncode}); continuing", flush=True)
            elif embedded_lyrics(path, read_tags):
                fetched += 1
            else:
                print(f"Beets lyrics: no result: {path.name}", flush=True)
        except Exception as error:
            # Do not expose credential-bearing request URLs or config contents.
            failures += 1
            print(f"Beets lyrics: skipped {path.name} ({type(error).__name__})", flush=True)
    print(f"Beets lyrics: fetched {fetched}, existing/instrumental {skipped}, failures {failures}", flush=True)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "library", "directory"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    import mutagen
    import yaml
    from beets.library import Library
    from mediafile import MediaFile

    # Private temporary overlay preserves provider order and prevents forced refresh
    # or fallback settings in a user's existing config from replacing local lyrics.
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    config.setdefault("lyrics", {}).update(auto=False, force=False, keep_synced=True, fallback=None)
    config.setdefault("import", {})["write"] = True
    def embed(path, text):
        audio = MediaFile(os.fspath(path))
        audio.lyrics = text
        audio.save()

    with tempfile.TemporaryDirectory(prefix="beets-lyrics-") as temp:
        overlay = Path(temp) / "config.yaml"
        overlay.write_text(yaml.safe_dump(config), encoding="utf-8")
        # Materialize the query before subprocesses write to the same SQLite DB.
        items = list(Library(os.fspath(args.library)).items())
        failures = process(items, args.directory,
                           overlay, args.library, mutagen.File, embed)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
