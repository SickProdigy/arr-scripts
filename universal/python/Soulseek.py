#!/usr/bin/env python3
"""Optional slskd wanted-list worker shared by Lidarr, Sonarr and Radarr.

Uses only the Python standard library. No Soularr code or dependency is bundled.
Persist intent before remote writes and reconcile batches after uncertain replies.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import shutil
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid
import xml.etree.ElementTree as ET


class ApiError(RuntimeError):
    def __init__(self, status=0):
        self.status = status
        super().__init__(f"API HTTP {status}" if status else "API unavailable")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Do not forward API credentials to redirected hosts.


class Api:
    def __init__(self, url, key):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.query:
            raise ValueError("Invalid API URL")
        if not key:
            raise ValueError("Missing API key")
        self.url, self.key = url.rstrip("/"), key
        self.opener = build_opener(NoRedirect())

    def call(self, path, data=None, **query):
        suffix = "?" + urlencode(query) if query else ""
        request = Request(self.url + "/" + path + suffix,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={"X-API-Key": self.key, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            status = error.code
            error.close()
            raise ApiError(status) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ApiError() from None


class SoulseekApi(Api):
    """Keep client-specific API versions outside the matching/import worker."""
    def __init__(self, url, key, client="slskd"):
        if client not in ("slskd", "nicotine"):
            raise ValueError("Select slskd or nicotine")
        super().__init__(url, key)
        self.client = client

    def call(self, path, data=None, **query):
        if self.client == "nicotine":
            path = path.replace("api/v0/", "api/v1/", 1)
        return super().call(path, data, **query)


def normalized(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", value, re.UNICODE))


def released(value, now):
    if not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp() <= now
    except ValueError:
        return False


def safe_remote(name):
    parts = PureWindowsPath(name).parts
    return bool(parts) and not any(p in ("..", ".") for p in parts) and "\x00" not in name and not re.search(r'[<>:"|?*]', PureWindowsPath(name).name)


def choose_files(target, responses):
    """Conservative identities; the Arr import decision remains authoritative."""
    candidates = []
    for response in responses:
        if not response.get("username") or response.get("lockedFileCount", 0) and not response.get("files"):
            continue
        groups = {}
        for file in response.get("files", []):
            name = file.get("filename", "")
            if not safe_remote(name) or file.get("size", 0) <= 0:
                continue
            path = PureWindowsPath(name)
            text = normalized(str(path))
            if target["app"] == "lidarr":
                if path.suffix.casefold() not in (".flac", ".mp3", ".m4a", ".opus", ".ogg"):
                    continue
                if normalized(target["artist"]) not in text or normalized(target["title"]) not in text:
                    continue
                parent = path.parent
                if re.fullmatch(r"(?:cd|disc|disk)\s*\d+", parent.name, re.I):
                    parent = parent.parent
                groups.setdefault((str(parent), path.suffix.casefold()), []).append(file)
            else:
                if path.suffix.casefold() not in (".mkv", ".mp4", ".avi", ".m4v", ".ts"):
                    continue
                stem = normalized(path.stem)
                title = normalized(target["title"])
                if not stem.startswith(title + " ") or re.search(r"\b(?:sample|trailer)\b", stem):
                    continue
                if target["app"] == "radarr":
                    if not stem[len(title):].strip().startswith(str(target["year"]) + " "):
                        if stem[len(title):].strip() != str(target["year"]):
                            continue
                else:
                    codes = re.findall(r"(?i)\bs(\d{1,2})e(\d{1,3})(?!\d|e)", path.stem)
                    if codes != [(str(target["season"]).zfill(2), str(target["episode"]).zfill(2))]:
                        # Accept unpadded numbers too, but reject multi-episode packs.
                        if len(codes) != 1 or tuple(map(int, codes[0])) != (target["season"], target["episode"]):
                            continue
                candidates.append((response, [file]))
        for (_, extension), files in groups.items():
            chosen, used = [], set()
            for track in target.get("tracks", []):
                wanted = normalized(track["title"])
                matches = []
                for file in files:
                    path = PureWindowsPath(file["filename"])
                    stem = normalized(re.sub(r"^\s*(?:\d{1,2}[-.]\d{1,3}|\d{1,3})[\s._-]+", "", path.stem))
                    if stem == wanted or stem == normalized(target["artist"]) + " " + wanted:
                        matches.append(file)
                if len(matches) != 1 or matches[0]["filename"] in used:
                    break
                chosen.append(matches[0])
                used.add(matches[0]["filename"])
            else:
                if chosen:
                    candidates.append((response, chosen))
    candidates.sort(key=lambda pair: (0 if all(f["filename"].lower().endswith(".flac") for f in pair[1]) else 1,
                                      pair[0].get("queueLength", 0), -pair[0].get("uploadSpeed", 0),
                                      pair[0]["username"], pair[1][0]["filename"]))
    return candidates[0] if candidates else None


def import_payload(app, target, decisions, folder):
    files = []
    root = Path(folder).resolve()
    for item in decisions:
        if item.get("rejections") or not item.get("quality"):
            continue
        path = Path(item["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        result = {key: item[key] for key in ("path", "quality", "languages", "releaseGroup", "indexerFlags", "folderName", "releaseType") if key in item}
        result["releaseGroup"] = item.get("releaseGroup") or "Soulseek"
        if app == "radarr":
            if (item.get("movie") or {}).get("id") != target["id"]:
                continue
            result["movieId"] = target["id"]
        elif app == "sonarr":
            episodes = item.get("episodes") or []
            if (item.get("series") or {}).get("id") != target["seriesId"] or [e["id"] for e in episodes] != [target["id"]]:
                continue
            result.update(seriesId=target["seriesId"], episodeIds=[target["id"]])
        else:
            tracks = item.get("tracks") or []
            if (item.get("album") or {}).get("id") != target["id"] or (item.get("artist") or {}).get("id") != target["artistId"]:
                continue
            if not tracks or any(t["id"] not in target["missingTrackIds"] for t in tracks):
                continue
            result.update(artistId=target["artistId"], albumId=target["id"],
                          albumReleaseId=item["albumReleaseId"], trackIds=[t["id"] for t in tracks],
                          disableReleaseSwitching=True)
        files.append(result)
    if app == "lidarr" and set(t for f in files for t in f["trackIds"]) != set(target["missingTrackIds"]):
        return None
    if app != "lidarr" and len(files) != 1:
        return None
    return {"name": "ManualImport", "files": files, "importMode": "copy"} if files else None


class Worker:
    def __init__(self, app, arr, slsk, config, downloads, delay=3600, sources="", audio=False, clock=time.time, imports=None):
        self.imports = Path(imports) if imports else None
        self.app, self.arr, self.slsk = app, arr, slsk
        self.config, self.downloads = Path(config), Path(downloads)
        self.delay, self.sources, self.audio, self.clock = delay, sources, audio, clock
        self.prefix = "api/v1/" if app == "lidarr" else "api/v3/"
        state = self.config / "extended" / "soulseek"
        state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state / "jobs.sqlite", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, target TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'waiting', first_seen REAL NOT NULL, next_run REAL NOT NULL DEFAULT 0, data TEXT NOT NULL DEFAULT '{}')")
        self.db.execute("CREATE TABLE IF NOT EXISTS scan (id INTEGER PRIMARY KEY CHECK(id=1), page INTEGER NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO scan(id,page) VALUES(1,1)")
        self.db.commit()

    def save(self, ident, state, data, wait=0):
        with self.db:
            self.db.execute("UPDATE jobs SET state=?,data=?,next_run=? WHERE id=?", (state, json.dumps(data), self.clock()+wait, ident))
        print(f"Soulseek {self.app}: item {ident}: {state}", flush=True)

    def pages(self, endpoint):
        page = 1
        while True:
            value = self.arr.call(self.prefix+endpoint, page=page, pageSize=100, sortKey="id", sortDirection="ascending")
            records = value["records"]
            yield from records
            if page*100 >= value["totalRecords"] or not records:
                break
            page += 1

    def discover(self):
        # Persist a small-page cursor so a large library cannot delay searches.
        page = self.db.execute("SELECT page FROM scan WHERE id=1").fetchone()[0]
        value = self.arr.call(self.prefix+"wanted/missing", page=page, pageSize=10,
                              sortKey="id", sortDirection="ascending")
        records = value["records"]
        next_page = page+1 if records and page*10 < value["totalRecords"] else 1
        return records, next_page, value["totalRecords"], page

    def targets(self, records):
        now = self.clock()
        for record in records:
            if not record.get("monitored"):
                continue
            ident = record["id"]
            if self.app == "radarr":
                if record.get("hasFile") or not released(record.get("digitalRelease") or record.get("physicalRelease") or record.get("inCinemas"), now):
                    continue
                target = dict(app=self.app, id=ident, title=record["title"], year=record["year"])
                target["query"] = f"{target['title']} {target['year']}"
            elif self.app == "sonarr":
                series = record.get("series") or self.arr.call(self.prefix+f"series/{record['seriesId']}")
                if not series.get("monitored") or record.get("hasFile") or not released(record.get("airDateUtc"), now):
                    continue
                if series.get("seriesType") != "standard":
                    continue  # Anime absolute numbering and daily episodes need a separate matcher.
                target = dict(app=self.app, id=ident, title=series["title"], seriesId=series["id"], season=record["seasonNumber"], episode=record["episodeNumber"])
                target["query"] = f"{target['title']} S{target['season']:02}E{target['episode']:02}"
            else:
                artist = record.get("artist") or self.arr.call(self.prefix+f"artist/{record['artistId']}")
                if not artist.get("monitored") or not released(record.get("releaseDate"), now):
                    continue
                album = self.arr.call(self.prefix+f"album/{ident}")
                releases = [r for r in album.get("releases", []) if r.get("monitored")]
                if len(releases) != 1:
                    continue
                tracks = self.arr.call(self.prefix+"track", albumId=ident, albumReleaseId=releases[0]["id"])
                missing = [t["id"] for t in tracks if not t.get("hasFile") and not t.get("trackFileId")]
                if not tracks or not missing:
                    continue
                target = dict(app=self.app, id=ident, title=record["title"], artist=artist["artistName"], artistId=artist["id"], tracks=tracks, missingTrackIds=missing, releaseDate=record.get("releaseDate"))
                target["query"] = f"{target['artist']} {target['title']}"
            yield target

    def queued(self):
        return list(self.pages("queue"))

    def blocked(self, target, queue):
        for entry in queue:
            if self.app == "radarr" and entry.get("movieId") == target["id"]:
                return True
            if self.app == "sonarr" and (entry.get("episodeId") == target["id"] or entry.get("seriesId") == target["seriesId"]):
                return True
            if self.app == "lidarr" and entry.get("albumId") == target["id"]:
                return True
        return False

    def ready(self, row, target):
        if self.app == "lidarr" and self.audio and self.sources != "soulseek":
            marker = self.config / "extended" / "soulseek" / "fallback" / str(target["id"])
            # This marker is written only after selected streaming providers missed.
            if marker.is_file():
                return True
            previous = self.config / "extended" / "logs" / "notfound"
            if self.clock()-row["first_seen"] < self.delay:
                return False
            if any(previous.glob(f"{target['id']}--*")):
                return True
            running = self.config / "extended" / "soulseek" / "audio-active"
            try:
                pid = int(running.read_text().strip())
                command = Path(f"/proc/{pid}/cmdline").read_bytes()
                return b"Audio" not in command
            except (OSError, ValueError):
                # Streaming worker unavailable/failed: allow the delayed fallback.
                return True
        delay = 0 if self.app == "lidarr" and self.sources == "soulseek" else self.delay
        return self.clock()-row["first_seen"] >= delay

    def batch_folder(self, data):
        batch = str(uuid.UUID(data["batch"]))
        if batch != data["batch"]:
            raise ValueError("Invalid batch directory")
        # New jobs use a flat destination. Keep existing transfer intents valid
        # until their already queued downloads can finish or be discarded.
        destination = data.get("destination", f"arr-scripts/{self.app}/{batch}")
        if destination not in (batch, f"arr-scripts/{self.app}/{batch}"):
            raise ValueError("Unexpected batch destination")
        folder = self.downloads.joinpath(*destination.split("/"))
        current = folder
        while current != self.downloads:
            if current.is_symlink():
                raise ValueError("Symlinked download directory")
            current = current.parent
        if self.downloads.is_symlink() or not folder.resolve().is_relative_to(self.downloads.resolve()):
            raise ValueError("Batch is outside download root")
        return folder

    def cleanup_import(self, target, data):
        """Verify the accepted command and library before deleting owned sources."""
        if "handoff" not in data:
            if "command" not in data:
                raise ValueError("Import acknowledgement unavailable")
            command = self.arr.call(self.prefix+f"command/{data['command']}")
            if command.get("status") != "completed":
                raise ValueError("Import has not succeeded")
        if self.app == "lidarr":
            tracks = self.arr.call(self.prefix+"track", albumId=target["id"])
            indexed = {track["id"]: track for track in tracks}
            library = []
            for ident in target["missingTrackIds"]:
                track = indexed.get(ident, {})
                if not track.get("trackFileId"):
                    raise ValueError("Imported track missing")
                library.append(self.arr.call(self.prefix+f"trackfile/{track['trackFileId']}"))
        else:
            kind = "movie" if self.app == "radarr" else "episode"
            item = self.arr.call(self.prefix+f"{kind}/{target['id']}")
            if not item.get("hasFile"):
                raise ValueError("Imported library item missing")
            file = item.get(kind+"File")
            if not file:
                file = self.arr.call(self.prefix+f"{kind}file/{item[kind+'FileId']}")
            library = [file]
        # Only UUID-owned batches may be cleaned; never follow directory symlinks.
        batch = str(uuid.UUID(data["batch"]))
        if batch != data["batch"]:
            raise ValueError("Invalid batch directory")
        folder = self.batch_folder(data)
        for file in library:
            path = Path(file["path"])
            if not path.is_absolute() or path.resolve().is_relative_to(self.downloads.resolve()):
                raise ValueError("Library file is inside download root")
            if self.imports and path.resolve().is_relative_to(self.imports.resolve()):
                raise ValueError("Library file is still in import root")
            if not path.is_file() or path.stat().st_size <= 0:
                raise ValueError("Library file unavailable")
        sources = []
        for file in data["files"]:
            name = file["filename"]
            if not safe_remote(name):
                raise ValueError("Invalid source filename")
            path = folder / PureWindowsPath(name).name
            if path.is_symlink():
                raise ValueError("Symlinked source file")
            if path.exists():
                if not path.is_file() or path.stat().st_size != file["size"]:
                    raise ValueError("Source file changed")
                if any(path.samefile(Path(item["path"])) for item in library):
                    raise ValueError("Source is the library file")
                sources.append(path)
        for path in sources:
            path.unlink()
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()

    def handoff(self, target, data, folder):
        """Publish a completed album atomically to Lidarr's existing watch folder."""
        if self.app != "lidarr" or self.imports is None:
            raise ValueError("Watched-folder handoff is only configured for Lidarr")
        if not self.imports.is_absolute() or self.imports.is_symlink():
            raise ValueError("Invalid import root")
        new_root = not self.imports.exists()
        self.imports.mkdir(parents=True, exist_ok=True)
        if new_root:
            self.imports.chmod(0o775)
            if hasattr(os,"geteuid") and os.geteuid() == 0:
                os.chown(self.imports,int(os.environ.get("PUID","1000")),int(os.environ.get("PGID","1000")))
        # Ask Lidarr to identify the release group, including rejected decisions.
        # Native completed-download handling owns the final import decision.
        clients = self.arr.call(self.prefix+"downloadclient")
        if not any(client.get("enable") and client.get("implementation") == "UsenetBlackhole"
                   and any(field.get("name") == "watchFolder" and field.get("value") == str(self.imports)
                           for field in client.get("fields", [])) for client in clients):
            raise ValueError("Enable a Usenet Blackhole client watching importPath")
        decisions = self.arr.call(self.prefix+"manualimport", folder=str(folder), filterExistingFiles="true")
        groups = {item.get("releaseGroup") for item in decisions if item.get("releaseGroup")}
        group = next(iter(groups)) if len(groups) == 1 else "Soulseek"
        def clean(value):
            return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:100] or "Unknown"
        year = str(target.get("releaseDate", ""))[:4]
        name = f"{clean(target['artist'])}-{clean(target['title'])}"
        if year.isdecimal():
            name += f" ({year})"
        name += f" [{clean(group)}]"
        destination = self.imports/name
        staging = self.imports.parent/(".soulseek-"+str(uuid.UUID(data["batch"])))
        owner = {"batch":data["batch"], "app":self.app, "id":target["id"]}
        marker = ".arr-soulseek.json"
        if destination.exists():
            if destination.is_symlink() or (destination/marker).is_symlink() or not (destination/marker).is_file() or json.loads((destination/marker).read_text()) != owner:
                raise ValueError("Import folder already belongs to another download")
        else:
            if staging.is_symlink():
                raise ValueError("Symlinked staging directory")
            staging.mkdir(exist_ok=True)
            ownership = staging/marker
            if ownership.exists():
                if ownership.is_symlink() or json.loads(ownership.read_text()) != owner:
                    raise ValueError("Staging ownership mismatch")
            else:
                if any(staging.iterdir()):
                    raise ValueError("Unowned staging directory")
                ownership.write_text(json.dumps(owner))
            # The downloader worker may run as root, but Lidarr imports as abc.
            def writable(path, directory=False):
                path.chmod(0o775 if directory else 0o664)
                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    os.chown(path, int(os.environ.get("PUID", "1000")), int(os.environ.get("PGID", "1000")))
            writable(staging, True)
            writable(ownership)
            for file in data["files"]:
                source = folder/PureWindowsPath(file["filename"]).name
                dest = staging/source.name
                if source.is_symlink() or dest.is_symlink():
                    raise ValueError("Symlinked batch file")
                shutil.copy2(source,dest)
                writable(dest)
                if dest.stat().st_size != file["size"]:
                    raise ValueError("Staging size mismatch")
            data["handoff"] = str(destination)
            data["importTarget"] = target
            self.save(target["id"], "publishing", data)
            os.rename(staging,destination)
        data["handoff"] = str(destination)
        data["importTarget"] = target
        # Persist the handoff before asking Arr to refresh. The worker must not
        # issue a competing direct import or republish a removed queue item.
        self.save(target["id"], "handoff", data, 60)
        # The published folder is now the sole completed-download copy, just as
        # for streaming providers. Never remove an original without its copy.
        for file in data["files"]:
            source = folder/PureWindowsPath(file["filename"]).name
            copied = destination/source.name
            if source.is_symlink() or copied.is_symlink() or not copied.is_file() or copied.stat().st_size != file["size"]:
                raise ValueError("Published batch changed; retained originals")
            if source.exists():
                if not source.is_file() or source.stat().st_size != file["size"]:
                    raise ValueError("Original batch changed; retained")
                source.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()
        self.arr.call(self.prefix+"command", {"name":"RefreshMonitoredDownloads"})

    def cleanup_handoff(self, target, data):
        """Verify the library using the existing cleanup before removing copies."""
        # Native handoff has no worker-owned ManualImport command. Completion
        # still requires every intended track to be present in the library.
        self.cleanup_import(target, data)
        folder = Path(data["handoff"])
        if folder.is_symlink() or not self.imports or folder.parent.resolve() != self.imports.resolve():
            raise ValueError("Invalid handed-off folder")
        if not folder.exists():
            return
        marker = folder/".arr-soulseek.json"
        if marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()).get("batch") != data["batch"]:
            raise ValueError("Handoff ownership mismatch")
        for file in data["files"]:
            path = folder/PureWindowsPath(file["filename"]).name
            if path.is_symlink():
                raise ValueError("Symlinked handoff file")
            if path.exists():
                if not path.is_file() or path.stat().st_size != file["size"]:
                    raise ValueError("Handed-off source changed")
                path.unlink()
        marker.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()

    def discard(self, ident, cancelled=False):
        """Remove only recorded, finished batch files; keep a retryable tombstone."""
        states = ("review","downloading","enqueueing") if cancelled else ("review",)
        row = self.db.execute("SELECT * FROM jobs WHERE id=? AND state IN (" + ",".join("?" for _ in states) + ")", (ident,*states)).fetchone()
        if not row:
            raise ValueError("Job is not eligible for discard")
        data = json.loads(row["data"])
        if "handoff" in data:
            raise ValueError("Remove this download through Lidarr's Activity queue")
        if "importTarget" in data and "command" not in data:
            raise ValueError("Import outcome is uncertain; retained")
        if "command" in data:
            command = self.arr.call(self.prefix+f"command/{data['command']}")
            if command.get("status") not in ("completed", "failed", "aborted"):
                raise ValueError("Import is still active; retained")
        sources = []
        folder = None
        if "batch" in data:
            batch = str(uuid.UUID(data["batch"]))
            if batch != data["batch"]:
                raise ValueError("Invalid batch directory")
            remote = self.slsk.call(f"api/v0/transfers/downloads/batches/{batch}")
            expected = {(f["filename"], f["size"]) for f in data["files"]}
            transfers = remote.get("transfers") or []
            if {(f["filename"], f["size"]) for f in transfers} != expected or not transfers:
                raise ValueError("Batch ownership cannot be verified; retained")
            if not cancelled and any(not f.get("state", "").startswith("Completed") for f in transfers):
                raise ValueError("Transfers are still active; retained")
            folder = self.batch_folder(data)
            if not folder.resolve().is_relative_to(self.downloads.resolve()):
                raise ValueError("Batch is outside download root")
            for file in data["files"]:
                if not safe_remote(file["filename"]):
                    raise ValueError("Invalid source filename")
                path = folder/PureWindowsPath(file["filename"]).name
                if path.is_symlink():
                    raise ValueError("Symlinked source file")
                if path.exists():
                    info = path.stat()
                    if not path.is_file() or info.st_size > file["size"] or info.st_nlink != 1:
                        raise ValueError("Source file changed or shared; retained")
                    sources.append(path)
        for path in sources:
            path.unlink()
        if folder and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
        self.save(ident, "discarded", {"reason": "cancelled by user" if cancelled else data.get("reason", "manually discarded")})

    def tick(self):
        version = self.slsk.call("api/v0/application/version")
        if isinstance(version, dict):
            parts = tuple(version.get(k, -1) for k in ("major", "minor", "patch"))
        else:
            match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+.]|$)", str(version))
            parts = tuple(map(int, match.groups())) if match else ()
        client = getattr(self.slsk, "client", "slskd")
        minimum = (0, 1, 0) if client == "nicotine" else (0, 26, 0)
        if not parts or parts < minimum:
            print(f"Soulseek: {client} API version is unsupported (minimum {'.'.join(map(str, minimum))})", flush=True)
            return
        application = self.slsk.call("api/v0/application")
        if client == "nicotine" and not {"search", "download-batches", "batch-destination"}.issubset(application.get("capabilities", [])):
            raise ValueError("Nicotine API capabilities missing")
        if not (application.get("server") or {}).get("isConnected"):
            print("Soulseek: slskd is disconnected; postponing searches", flush=True)
            return
        queue = self.queued()  # Failure must abort: do not assume an empty queue.
        now = self.clock()
        # Reconcile owned transfers first, even when they are outside this page.
        rows = self.db.execute("SELECT * FROM jobs WHERE next_run<=? AND state NOT IN ('done','review','discarded') "
                               "ORDER BY CASE WHEN state='waiting' THEN 1 ELSE 0 END,next_run,first_seen,id LIMIT 20",
                               (now,)).fetchall()
        records, next_page, total, page = self.discover()
        kind = {"lidarr": "album", "sonarr": "episode", "radarr": "movie"}[self.app]
        page_ids = {record["id"] for record in records}
        priority_markers = {}
        # Streaming misses should not wait for the wanted-list cursor to reach
        # their album. Prioritize one marker without changing the cap.
        if self.app == "lidarr" and self.audio and self.sources != "soulseek":
            priority = 0
            for marker in (self.config/"extended/soulseek/fallback").glob("*"):
                if not marker.name.isdecimal() or not marker.is_file():
                    continue
                ident = int(marker.name)
                if ident in page_ids:
                    continue
                job = self.db.execute("SELECT state,next_run FROM jobs WHERE id=?", (ident,)).fetchone()
                if job and (job["state"] != "waiting" or job["next_run"] > now):
                    continue
                try:
                    records.append(self.arr.call(self.prefix+f"album/{ident}"))
                except ApiError as error:
                    if error.status != 404:
                        raise
                    marker.unlink(missing_ok=True)
                    continue
                priority_markers[ident] = marker
                page_ids.add(ident)
                priority += 1
                if priority >= 1:
                    break
        targets = {t["id"]: t for t in self.targets(records)}
        for ident, marker in priority_markers.items():
            if ident not in targets:
                marker.unlink(missing_ok=True)  # No longer a monitored missing item.
        for row in rows:
            if row["id"] in page_ids:
                continue
            try:
                record = self.arr.call(self.prefix+f"{kind}/{row['id']}")
            except ApiError as error:
                if error.status != 404:
                    raise
                continue  # Deleted items are no longer eligible.
            targets.update({t["id"]: t for t in self.targets([record])})
        with self.db:
            for ident, target in targets.items():
                self.db.execute("INSERT INTO jobs(id,target,first_seen) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET target=excluded.target,state=CASE WHEN jobs.state='done' THEN 'waiting' ELSE jobs.state END", (ident, json.dumps(target), now))
            self.db.execute("UPDATE scan SET page=? WHERE id=1", (next_page,))
        print(f"Soulseek {self.app}: wanted page {page}; {len(records)} of {total} items; {len(targets)} eligible", flush=True)
        # Only absence from a refreshed record proves completion; absence from a
        # discovery page must never finish jobs elsewhere in the library.
        ids = {row["id"] for row in rows} | set(targets)
        rows = self.db.execute("SELECT * FROM jobs WHERE next_run<=? AND state NOT IN ('done','review','discarded') "
                               "AND id IN (" + ",".join("?" for _ in ids) + ") "
                               "ORDER BY CASE WHEN state='waiting' THEN 1 ELSE 0 END,first_seen,id",
                               (now, *sorted(ids))).fetchall() if ids else []
        rows.sort(key=lambda row: (row["state"] == "waiting",
                                  row["id"] not in priority_markers,
                                  row["first_seen"], row["id"]))
        searches = 0
        active = self.db.execute("SELECT count(*) FROM jobs WHERE state IN ('searching','enqueueing','downloading','downloaded','importing','handoff','publishing')").fetchone()[0]
        for row in rows:
            ident, state, data = row["id"], row["state"], json.loads(row["data"])
            target = targets.get(ident)
            if target is None:
                if state in ("importing", "cleanup", "handoff", "publishing"):
                    try:
                        original = data.get("importTarget", json.loads(row["target"]))
                        if "handoff" in data:
                            self.cleanup_handoff(original, data)
                        else:
                            self.cleanup_import(original, data)
                    except (ApiError, ValueError, KeyError, TypeError, OSError) as error:
                        print(f"Soulseek {self.app}: item {ident}: cleanup {type(error).__name__}; retained", flush=True)
                        self.save(ident, "cleanup", data, 300)
                        continue
                if state != "done":
                    self.save(ident, "done", data)
                continue
            if state in ("done", "review") or (state not in ("handoff","publishing") and self.blocked(target, queue)):
                continue
            try:
                if state == "waiting":
                    if searches >= 1 or active >= 1 or not self.ready(row, target):
                        with self.db:
                            self.db.execute("UPDATE jobs SET next_run=? WHERE id=?", (now+300, ident))
                        continue
                    searches += 1
                    active += 1
                    data = {"search": str(uuid.uuid4())}
                    self.save(ident, "searching", data, 30)
                    self.slsk.call("api/v0/searches", {"id": data["search"], "searchText": target["query"], "searchTimeout": 15000, "responseLimit": 100, "fileLimit": 10000, "filterResponses": True})
                elif state == "searching":
                    try:
                        result = self.slsk.call(f"api/v0/searches/{data['search']}", includeResponses="true")
                    except ApiError as error:
                        if error.status == 404:
                            self.save(ident, "waiting", {}, 300)
                            continue
                        raise
                    if not result.get("isComplete"):
                        continue
                    choice = choose_files(target, result.get("responses", []))
                    if not choice:
                        self.save(ident, "waiting", {}, 86400)
                        continue
                    peer, files = choice
                    names = [PureWindowsPath(f["filename"]).name.casefold() for f in files]
                    if len(names) != len(set(names)):
                        self.save(ident, "review", {"reason": "duplicate basenames"})
                        continue
                    data.update(batch=str(uuid.uuid4()), username=peer["username"], files=[{"filename": f["filename"], "size": f["size"]} for f in files])
                    data["destination"] = data["batch"]
                    self.save(ident, "enqueueing", data)
                elif state == "enqueueing":
                    try:
                        self.slsk.call(f"api/v0/transfers/downloads/batches/{data['batch']}")
                    except ApiError as error:
                        if error.status != 404:
                            raise
                        try:
                            self.slsk.call("api/v0/transfers/downloads/batches", {"id": data["batch"], "searchId": data["search"], "username": data["username"], "files": data["files"], "options": {"destination": data.get("destination", f"arr-scripts/{self.app}/{data['batch']}")}})
                        except ApiError as error:
                            if error.status != 409:
                                raise
                    self.save(ident, "downloading", data, 60)
                elif state == "downloading":
                    batch = self.slsk.call(f"api/v0/transfers/downloads/batches/{data['batch']}")
                    transfers = batch.get("transfers") or []
                    expected = {(f["filename"], f["size"]) for f in data["files"]}
                    if {(f["filename"], f["size"]) for f in transfers} != expected:
                        self.save(ident, "review", {**data, "reason": "partial enqueue"})
                    elif all(f["state"] == "Completed, Succeeded" for f in transfers):
                        self.save(ident, "downloaded", data)
                    elif any("Completed" in f["state"] and "Succeeded" not in f["state"] for f in transfers):
                        self.save(ident, "review", {**data, "reason": "failed transfer"})
                elif state in ("downloaded","publishing"):
                    folder = self.batch_folder(data)
                    if not folder.is_dir():
                        raise ValueError("Download mount unavailable")
                    for file in data["files"]:
                        local = (folder / PureWindowsPath(file["filename"]).name).resolve()
                        if not local.is_relative_to(folder.resolve()) or not local.is_file() or local.stat().st_size != file["size"]:
                            raise ValueError("Completed files unavailable or size mismatch")
                    if self.app == "lidarr" and self.imports is not None:
                        self.handoff(target, data, folder)
                        continue
                    decisions = self.arr.call(self.prefix+"manualimport", folder=str(folder), filterExistingFiles="true")
                    payload = import_payload(self.app, target, decisions, folder)
                    if not payload:
                        self.save(ident, "review", {**data, "reason": "import rejected or identity mismatch"})
                        continue
                    # A lost response may have accepted the import: don't blindly resubmit.
                    data["importTarget"] = target
                    self.save(ident, "importing", data, 60)
                    result = self.arr.call(self.prefix+"command", payload)
                    data["command"] = result["id"]
                    self.save(ident, "importing", data, 60)
                elif state == "handoff":
                    owned = [entry for entry in queue if entry.get("outputPath") == data["handoff"]]
                    if owned:
                        continue  # Lidarr owns import, removal, and manual retry.
                    if not Path(data["handoff"]).exists():
                        self.save(ident, "discarded", {"reason":"removed from Lidarr before import"})
                    # It may not be visible until the next native queue refresh.
                elif state == "importing":
                    if "command" not in data:
                        self.save(ident, "review", {**data, "reason": "uncertain import; inspect library"})
                        continue
                    result = self.arr.call(self.prefix+f"command/{data['command']}")
                    if result["status"] in ("completed", "failed", "aborted"):
                        self.save(ident, "review", {**data, "reason": "import finished but item remains missing"})
            except (ApiError, ValueError, KeyError, TypeError, OSError) as error:
                # Never log API response bodies, credentials or peer-supplied URLs.
                print(f"Soulseek {self.app}: item {ident}: {type(error).__name__}; deferred", flush=True)
                # Reconcile persisted intent on next run, including uncertain POSTs.
                with self.db:
                    self.db.execute("UPDATE jobs SET next_run=? WHERE id=?", (now+300, ident))


@contextmanager
def worker_lock(config):
    import fcntl  # The production worker runs in Linux containers.
    path = Path(config) / "extended" / "soulseek"
    path.mkdir(parents=True, exist_ok=True)
    with (path / "worker.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--retry", type=int, help="Retry a reviewed or discarded job after fixing its cause")
    actions.add_argument("--discard", type=int, help="Discard a reviewed job and its finished batch files")
    parser.add_argument("--owns", type=int, help="Local-only check: exit 0 if Soulseek owns this active job")
    parser.add_argument("--diagnose", action="store_true", help="Read-only API and wanted-item diagnostics, independent of the worker lock")
    parser.add_argument("--cancelled", action="store_true", help="With --discard: confirm transfers were already cancelled and cleared in the client")
    args = parser.parse_args()
    if args.cancelled and args.discard is None:
        parser.error("--cancelled requires --discard")
    config = os.environ.get("SOULSEEK_CONFIG_DIR", "/config")
    if args.owns is not None:
        database = Path(config)/"extended/soulseek/jobs.sqlite"
        if not database.is_file():
            raise SystemExit(1)
        try:
            with sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True, timeout=5) as connection:
                row = connection.execute("SELECT state FROM jobs WHERE id=?", (args.owns,)).fetchone()
        except sqlite3.Error:
            raise SystemExit(0)  # Unreadable state: avoid racing an uncertain job.
        raise SystemExit(0 if row and row[0] in ("searching", "enqueueing", "downloading", "downloaded", "importing", "handoff", "publishing") else 1)
    app = os.environ["SOULSEEK_APP"]
    if app not in ("lidarr", "sonarr", "radarr"):
        raise ValueError("Unsupported Arr application")
    url, key = os.environ.get("SOULSEEK_ARR_URL"), os.environ.get("SOULSEEK_ARR_API_KEY")
    if not url or not key:
        root = ET.parse(Path(config)/"config.xml").getroot()
        url = url or f"http://127.0.0.1:{root.findtext('Port')}/{(root.findtext('UrlBase') or '').strip('/')}"
        key = key or root.findtext("ApiKey")
    downloads = Path(os.environ["SOULSEEK_DOWNLOAD_PATH"])
    if not downloads.is_absolute() or not downloads.is_dir():
        raise ValueError("Mount slskd's completed download directory first")
    delay = int(os.environ.get("SOULSEEK_FALLBACK_DELAY", "3600"))
    if delay < 0:
        raise ValueError("Fallback delay must be nonnegative")
    if args.diagnose:
        slsk = SoulseekApi(os.environ["SOULSEEK_URL"], os.environ["SOULSEEK_API_KEY"], os.environ.get("SOULSEEK_CLIENT", "slskd"))
        print(f"Soulseek diagnostic: client={slsk.client}; download mount available", flush=True)
        print("Client API version:", slsk.call("api/v0/application/version"), flush=True)
        application = slsk.call("api/v0/application")
        print("Soulseek connected:", bool((application.get("server") or {}).get("isConnected")), flush=True)
        arr = Api(url, key)
        prefix = "api/v1/" if app == "lidarr" else "api/v3/"
        missing = arr.call(prefix+"wanted/missing", page=1, pageSize=5, sortKey="id", sortDirection="ascending")
        print("Arr wanted/missing total:", missing.get("totalRecords"), flush=True)
        for record in missing.get("records", []):
            print("Sample item:", record["id"], "monitored:", record.get("monitored"), flush=True)
            if app == "lidarr":
                album = arr.call(prefix+f"album/{record['id']}")
                selected = [r for r in album.get("releases", []) if r.get("monitored")]
                print("  monitored releases:", len(selected), "release date:", record.get("releaseDate"), flush=True)
                if len(selected) == 1:
                    tracks = arr.call(prefix+"track", albumId=record["id"], albumReleaseId=selected[0]["id"])
                    print("  tracks:", len(tracks), "missing:", sum(not t.get("hasFile") and not t.get("trackFileId") for t in tracks), flush=True)
        return
    with worker_lock(config) as acquired:
        if not acquired:
            if args.discard is not None or args.retry is not None:
                print("Soulseek: worker busy; rerun this command after the current pass", flush=True)
                raise SystemExit(1)
            return
        worker = Worker(app, Api(url, key), SoulseekApi(os.environ["SOULSEEK_URL"], os.environ["SOULSEEK_API_KEY"], os.environ.get("SOULSEEK_CLIENT", "slskd")),
                        config, downloads, delay, os.environ.get("SOULSEEK_SOURCES", ""),
                        os.environ.get("SOULSEEK_AUDIO_ENABLED") == "true",
                        imports=os.environ.get("SOULSEEK_IMPORT_PATH", "/downloads/lidarr/import") if app == "lidarr" else None)
        try:
            if args.discard is not None:
                worker.discard(args.discard, cancelled=args.cancelled)
                return  # Discard must never start unrelated searches.
            if args.retry is not None:
                row = worker.db.execute("SELECT * FROM jobs WHERE id=? AND state IN ('review','discarded')", (args.retry,)).fetchone()
                if not row:
                    raise ValueError("Job is not awaiting review")
                data = json.loads(row["data"])
                worker.save(args.retry, "downloading" if "batch" in data else "waiting", data)
            worker.tick()
        finally:
            worker.db.close()


if __name__ == "__main__":
    try:
        main()
    except (ApiError, ValueError, KeyError, OSError, ET.ParseError) as error:
        detail = str(error) if isinstance(error, ApiError) else type(error).__name__
        print(f"Soulseek: {detail}; check configuration and API availability", flush=True)
        raise SystemExit(1)
