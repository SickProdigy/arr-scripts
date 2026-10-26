#!/usr/bin/env python3
"""Incrementally compare monitored Lidarr artists with their Apple Music catalogs."""

import argparse
import json
import os
import re
import shlex
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

CONFIG = Path(os.environ.get("ARR_EXTENDED_CONF", "/config/extended.conf"))
LIDARR_CONFIG = Path(os.environ.get("LIDARR_CONFIG_XML", "/config/config.xml"))
STATE_DIR = Path(os.environ.get("MUSICBRAINZ_CATALOG_STATE", "/config/extended/musicbrainz-helper/catalog"))
STATE_FILE = STATE_DIR / "state.json"
APPLE_RE = re.compile(r"https?://(?:music\.)?apple\.com/[^\s]+", re.I)


def read_config(path=CONFIG):
    values = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        try:
            parsed = shlex.split(value, comments=True, posix=True)
        except ValueError:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and len(parsed) <= 1:
            values[name] = parsed[0] if parsed else ""
    return values


def positive_int(value, default):
    try:
        value = int(value)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def lidarr_connection(path=LIDARR_CONFIG):
    root = ET.parse(path).getroot()
    port = root.findtext("Port") or "8686"
    url_base = (root.findtext("UrlBase") or "").strip("/")
    base = f"http://127.0.0.1:{port}" + (f"/{url_base}" if url_base else "")
    api_key = root.findtext("ApiKey") or ""
    if not api_key:
        raise RuntimeError("Lidarr API key is unavailable")
    return base, api_key


def lidarr_artists():
    base, api_key = lidarr_connection()
    request = urllib.request.Request(base + "/api/v1/artist", headers={"X-Api-Key": api_key})
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)
    if not isinstance(value, list):
        raise RuntimeError("Lidarr returned an invalid artist list")
    return value


def apple_url(artist):
    for link in artist.get("links") or []:
        url = link.get("url", "") if isinstance(link, dict) else str(link)
        match = APPLE_RE.search(url)
        if match:
            return match.group(0)
    return ""


def load_state():
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    staged = STATE_FILE.with_suffix(".json.tmp")
    staged.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, STATE_FILE)


def helper_result(output):
    decoder = json.JSONDecoder()
    for offset, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[offset:])
        except ValueError:
            continue
        if isinstance(value, dict) and "release_seeds" in value:
            return value
    raise RuntimeError("musicbrainz-new-artist did not return its JSON summary")


def scan(force=False):
    config = read_config()
    if config.get("enableMusicBrainzCatalog", "false").lower() != "true" and not force:
        print("MusicBrainz catalog review is disabled")
        return 0
    batch = positive_int(config.get("musicBrainzCatalogBatchSize"), 5)
    days = positive_int(config.get("musicBrainzCatalogDays"), 30)
    country = config.get("musicBrainzCatalogCountry", "US").upper()
    now = int(time.time())
    state = load_state()
    records = state.setdefault("artists", {})
    candidates = []
    monitored = 0
    missing_links = 0
    for artist in lidarr_artists():
        if not artist.get("monitored"):
            continue
        monitored += 1
        mbid = str(artist.get("foreignArtistId") or "").strip()
        if not mbid:
            continue
        record = records.setdefault(mbid, {})
        record["artist"] = artist.get("artistName") or artist.get("sortName") or mbid
        record["apple_url"] = apple_url(artist)
        if not record["apple_url"]:
            missing_links += 1
            record["status"] = "missing-apple-link"
            continue
        candidates.append((int(record.get("scanned_at") or 0), mbid, record))
    candidates.sort(key=lambda row: (row[0], row[2]["artist"].casefold()))
    due_before = now - days * 86400
    selected = [row for row in candidates if force or row[0] < due_before][:batch]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for _, mbid, record in selected:
        report = STATE_DIR / f"{mbid}.html"
        command = ["musicbrainz-new-artist", record["apple_url"], "--artist-mbid", mbid,
                   "--country", country, "--output", str(report)]
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=900)
            if completed.returncode:
                raise RuntimeError((completed.stderr or completed.stdout or "helper failed").strip()[-1000:])
            result = helper_result(completed.stdout)
            record.update({
                "status": "review" if int(result.get("release_seeds") or 0) else "current",
                "apple_releases": int(result.get("apple_releases") or 0),
                "musicbrainz_releases": int(result.get("exact_musicbrainz_releases") or 0),
                "release_seeds": int(result.get("release_seeds") or 0),
                "report": report.name,
                "scanned_at": int(time.time()),
                "error": "",
            })
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            record.update({"status": "error", "scanned_at": int(time.time()), "error": str(error)})
        save_state(state)
    state.update({"last_run": now, "monitored_artists": monitored,
                  "eligible_artists": len(candidates), "missing_apple_links": missing_links})
    save_state(state)
    print(json.dumps({"scanned": len(selected), "monitored": monitored,
                      "eligible": len(candidates), "missing_apple_links": missing_links}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="scan the next batch without waiting for its due date")
    args = parser.parse_args(argv)
    return scan(args.force)


if __name__ == "__main__":
    raise SystemExit(main())
