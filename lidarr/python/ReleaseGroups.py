#!/usr/bin/env python3
"""Audit or restore Lidarr TrackFile release groups from trusted filename suffixes."""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import PurePath
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


KNOWN_GROUPS = {name.casefold(): name for name in ("Deezer", "Tidal", "Soulseek", "Deemix")}
SUFFIX = re.compile(r"-(Deezer|Tidal|Soulseek|Deemix)$", re.IGNORECASE)


class Lidarr:
    def __init__(self, url, key):
        self.url = url.rstrip("/") + "/api/v1"
        self.key = key

    def call(self, path, data=None, method=None, **query):
        suffix = "?" + urlencode(query, doseq=True) if query else ""
        request = Request(
            f"{self.url}/{path}{suffix}",
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={"X-Api-Key": self.key, "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}


def group_from_path(path):
    match = SUFFIX.search(PurePath(path).stem)
    return KNOWN_GROUPS[match.group(1).casefold()] if match else None


def candidates(client, album_id=None):
    if album_id is not None:
        yield from client.call("trackfile", albumId=album_id)
        return
    for artist in client.call("artist"):
        yield from client.call("trackfile", artistId=artist["id"])


def run(client, apply=False, album_id=None, expected_group=None, stream=sys.stdout):
    selected = updated = preserved = 0
    expected = KNOWN_GROUPS.get(expected_group.casefold()) if expected_group else None
    for track_file in candidates(client, album_id):
        inferred = group_from_path(track_file.get("path", ""))
        if not inferred or expected and inferred != expected:
            continue
        selected += 1
        current = track_file.get("releaseGroup")
        if current:
            preserved += 1
            continue
        action = "update" if apply else "would-update"
        print(json.dumps({"action": action, "id": track_file["id"], "releaseGroup": inferred,
                          "path": track_file["path"]}, ensure_ascii=False), file=stream, flush=True)
        if apply:
            track_file["releaseGroup"] = inferred
            client.call(f"trackfile/{track_file['id']}", track_file, method="PUT")
            updated += 1
    summary = {"selected": selected, "updated": updated, "already_set": preserved, "apply": apply}
    print(json.dumps({"summary": summary}), file=stream, flush=True)
    return summary


def credentials(config):
    root = ET.parse(config).getroot()
    key = root.findtext("ApiKey")
    if not key:
        raise ValueError("Lidarr API key is missing")
    return key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write proposed release-group repairs")
    parser.add_argument("--album-id", type=int, help="limit processing to one Lidarr album")
    parser.add_argument("--set-group", choices=tuple(KNOWN_GROUPS.values()),
                        help="only accept this recognized filename suffix")
    parser.add_argument("--url", default="http://localhost:8686")
    parser.add_argument("--config", default="/config/config.xml")
    args = parser.parse_args(argv)
    try:
        run(Lidarr(args.url, credentials(args.config)), args.apply, args.album_id, args.set_group)
    except (HTTPError, URLError, OSError, ValueError, ET.ParseError) as error:
        print(f"Release-group repair failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
