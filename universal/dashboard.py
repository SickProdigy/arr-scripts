#!/usr/bin/env python3
"""Small, dependency-free web dashboard for approved arr-scripts commands."""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


CONFIG = Path(os.environ.get("ARR_EXTENDED_CONF", "/config/extended.conf"))
STATE_DIR = Path(os.environ.get("ARR_DASHBOARD_STATE", "/config/extended/dashboard"))
LOG_DIR = Path(os.environ.get("ARR_LOG_DIR", "/config/logs"))
DEFAULT_PORTS = {"lidarr": 8687, "sonarr": 8990, "radarr": 7879, "readarr": 8788}
REPLAYGAIN_STATE_DIR = Path(os.environ.get("REPLAYGAIN_STATE_DIR", "/config/extended/replaygain"))
SONICRATINGS_STATE_DIR = Path(os.environ.get("SONICRATINGS_STATE_DIR", "/config/extended/sonicratings"))
MUSICBRAINZ_STATE_DIR = Path(os.environ.get("MUSICBRAINZ_STATE_DIR", "/config/extended/musicbrainz-helper"))
MUSICBRAINZ_CATALOG_DIR = Path(os.environ.get("MUSICBRAINZ_CATALOG_STATE", str(MUSICBRAINZ_STATE_DIR / "catalog")))
RATINGS_HELPER_STATE_DIR = Path(os.environ.get("RATINGS_HELPER_STATE_DIR", "/config/extended/ratings-helper"))
TIDAL_CONFIG_DIR = Path(os.environ.get("TIDAL_CONFIG_DIR", "/config/extended/tidal_dl_sg"))
DEEMIX_ARL_FILE = Path(os.environ.get("DEEMIX_ARL_FILE", "/config/xdg/deemix/.arl"))
CONFIG_FILES = {
    "beets": (Path("/config/extended/beets-config.yaml"), "Beets - post-import enrichment"),
    "genre-whitelist": (Path("/config/extended/beets-genre-whitelist.txt"), "Beets genre whitelist"),
}
SECRET_NAME = re.compile(r"(?:password|token|secret|api_?key|arl$)", re.I)
CONFIG_GROUPS = (
    ("Dashboard", ("enableDashboard", "dashboardBindAddress", "dashboardPort", "dashboardUsername", "dashboardPassword", "dashboardPreviewMaxAgeHours")),
    ("ReplayGain", ("enableReplaygainTags", "enableReplaygainMaintenance", "replaygainMaintenanceDays", "replaygainTargetLoudness", "replaygainThreads", "replaygainPreserveMtime", "replaygainClipMode", "replaygainTruePeak", "replaygainMaxPeak")),
    ("Lyrics", ("enableLyrics", "enableLyricsMaintenance", "lyricsMaintenanceDays", "lyricsLibraryRoot", "lyricsTimeout", "lyricsUrl")),
    ("MusicBrainz", ("enableMusicBrainzHelper", "musicBrainzUsername", "musicBrainzPassword", "musicBrainzToken", "musicBrainzOAuthClientId", "musicBrainzOAuthClientSecret", "musicBrainzOAuthRedirectUri", "enableMusicBrainzCatalog", "musicBrainzCatalogBatchSize", "musicBrainzCatalogDays", "musicBrainzCatalogCountry")),
    ("Ratings", ("enableRatings", "subsonicUrl", "subsonicUsername", "subsonicPassword", "lastfmApiKey", "ratingsProvider", "spotifyClientId", "spotifyClientSecret", "tidalClientId", "tidalClientSecret", "discogsToken", "discogsInheritAlbumRating")),
    ("Soulseek", ("enableSoulseek", "soulseekClient", "soulseekUrl", "soulseekApiKey", "soulseekDownloadPath", "soulseekMaxBatches", "soulseekMaxForegroundBatches")),
    ("Library maintenance", ("enableLibraryMaintenance", "libraryMaintenanceDays", "libraryMaintenanceDateAudit", "libraryMaintenanceRoot")),
)


def read_config(path=CONFIG):
    values = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        assignment, _ = _split_comment(line.strip())
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", assignment)
        if not match:
            continue
        try:
            parsed = shlex.split(match.group(2), posix=True)
        except ValueError:
            continue
        if len(parsed) <= 1:
            values[match.group(1)] = parsed[0] if parsed else ""
    return values


def _split_comment(line):
    quote = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\" and quote == '"':
            escaped = True
        elif character in "'\"":
            quote = None if quote == character else (character if quote is None else quote)
        elif character == "#" and quote is None:
            return line[:index].rstrip(), line[index:]
    return line.rstrip(), ""


def _shell_quote(value):
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Configuration values must fit on one line")
    return "'" + value.replace("'", "'\"'\"'") + "'"


def backup_configuration(path, keep=5):
    path = Path(path)
    backup_dir = STATE_DIR / "backups" / path.name
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    backup = backup_dir / (path.name + "." + stamp + ".bak")
    shutil.copy2(path, backup)
    existing = sorted(backup_dir.glob(path.name + ".*.bak"),
                      key=lambda item: item.stat().st_mtime, reverse=True)
    for old in existing[keep:]:
        old.unlink()
    return backup


def configuration_fields(path=CONFIG):
    values = read_config(path)
    comments = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []
    for line in lines:
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=", line)
        if match:
            comments[match.group(1)] = _split_comment(line)[1].lstrip("# ")
    result = []
    known = set()
    for group, names in CONFIG_GROUPS:
        fields = []
        for name in names:
            if name not in values:
                continue
            known.add(name)
            secret = bool(SECRET_NAME.search(name))
            fields.append({"name": name, "value": "" if secret else values[name],
                           "configured": bool(values[name]), "secret": secret,
                           "description": comments.get(name, "")})
        if fields:
            result.append({"name": group, "fields": fields})
    other = [{"name": name, "value": value, "configured": bool(value), "secret": False,
              "description": comments.get(name, "")}
             for name, value in values.items() if name not in known and not SECRET_NAME.search(name)]
    if other:
        result.append({"name": "Other", "fields": other})
    return result


def update_configuration(changes, path=CONFIG):
    if not isinstance(changes, dict) or len(changes) > 100:
        raise ValueError("Invalid configuration update")
    path = Path(path)
    current = read_config(path)
    unknown = set(changes) - set(current)
    if unknown:
        raise ValueError("Unknown configuration setting: " + sorted(unknown)[0])
    normalized = {}
    for name, value in changes.items():
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("Invalid value for " + name)
        normalized[name] = value
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    output = []
    for line in lines:
        match = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)=", line)
        if not match or match.group(2) not in normalized:
            output.append(line)
            continue
        _, comment = _split_comment(line)
        suffix = (" " + comment) if comment else ""
        output.append(f"{match.group(1)}{match.group(2)}={_shell_quote(normalized[match.group(2)])}{suffix}")
    backup = backup_configuration(path)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text("\n".join(output) + "\n", encoding="utf-8", newline="\n")
    os.replace(staged, path)
    return backup


def auxiliary_configuration(name):
    entry = CONFIG_FILES.get(name)
    if not entry:
        raise ValueError("Unknown configuration file")
    path, label = entry
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ValueError(f"Configuration file is unavailable: {label}") from error
    return {"name": name, "label": label, "content": content}


def update_auxiliary_configuration(name, content):
    entry = CONFIG_FILES.get(name)
    if not entry:
        raise ValueError("Unknown configuration file")
    if not isinstance(content, str) or not content.strip() or len(content) > 65536 or "\x00" in content:
        raise ValueError("Configuration content must be between 1 byte and 64 KiB")
    path, _ = entry
    if not path.is_file():
        raise ValueError("Configuration file is unavailable")
    backup = backup_configuration(path)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(content.rstrip("\r\n") + "\n", encoding="utf-8", newline="\n")
    os.replace(staged, path)
    return backup


def truthy(value):
    return str(value).casefold() in {"1", "true", "yes", "on"}


def lidarr_tasks(config):
    root = config.get("libraryMaintenanceRoot") or config.get("lyricsLibraryRoot") or "/music"
    tasks = {
        "replaygain-audit": {
            "name": "Audit ReplayGain tags", "kind": "preview", "enabled": truthy(config.get("enableReplaygainTags")),
            "workflow": "ReplayGain audit", "action": "Audit now",
            "command": ["bash", "/config/extended/ReplayGainTagger.bash", "--audit", "--path", root],
            "description": "Count complete, partial, and missing ReplayGain tags without changing files.",
        },
        "replaygain-preview": {
            "name": "Preview ReplayGain backfill", "kind": "preview", "enabled": truthy(config.get("enableReplaygainTags")),
            "workflow": "ReplayGain backfill", "action": "Preview",
            "command": ["bash", "/config/extended/ReplayGainTagger.bash", "--backfill", "--dry-run", "--path", root],
            "description": "Show which albums need ReplayGain tags.",
        },
        "replaygain-backfill": {
            "name": "Run ReplayGain backfill", "kind": "write", "enabled": truthy(config.get("enableReplaygainTags")),
            "workflow": "ReplayGain backfill", "action": "Run now",
            "preview": "replaygain-preview", "command": ["bash", "/config/extended/ReplayGainTagger.bash", "--backfill", "--path", root],
            "description": "Write ReplayGain metadata to albums found by the preview.",
        },
        "lyrics-preview": {
            "name": "Preview missing lyrics", "kind": "preview", "enabled": truthy(config.get("enableLyrics")),
            "workflow": "Missing lyrics", "action": "Preview",
            "command": ["python3", "/config/extended/python/Lyrics.py", "--audit", config.get("lyricsLibraryRoot", "/music")],
            "description": "Count audio files missing embedded lyrics without queuing or writing.",
        },
        "lyrics-backfill": {
            "name": "Queue missing lyrics", "kind": "write", "enabled": truthy(config.get("enableLyrics")),
            "workflow": "Missing lyrics", "action": "Queue now",
            "preview": "lyrics-preview",
            "command": ["python3", "/config/extended/python/Lyrics.py", "--enqueue", config.get("lyricsLibraryRoot", "/music"), "--strict-enqueue"],
            "description": "Scan the library and queue files missing lyrics for the background worker.",
        },
        "ratings-preview": {
            "name": "Preview ratings backfill", "kind": "preview", "section": "ratings", "enabled": truthy(config.get("enableRatings")),
            "workflow": "Ratings backfill", "action": "Preview",
            "command": ["bash", "/config/extended/Ratings.bash", "--library", "--preview"],
            "description": "Look up library ratings without submitting changes.",
        },
        "ratings-backfill": {
            "name": "Run ratings backfill", "kind": "write", "section": "ratings", "enabled": truthy(config.get("enableRatings")),
            "workflow": "Ratings backfill", "action": "Run now",
            "preview": "ratings-preview", "command": ["bash", "/config/extended/Ratings.bash", "--library"],
            "description": "Fill unrated library items using the configured provider.",
        },
        "release-groups-preview": {
            "name": "Preview release-group repair", "kind": "preview", "enabled": True,
            "workflow": "Release-group repair", "action": "Preview",
            "command": ["python3", "/config/extended/python/ReleaseGroups.py"],
            "description": "Find blank Lidarr release groups recoverable from trusted filename suffixes.",
        },
        "release-groups-apply": {
            "name": "Apply release-group repair", "kind": "write", "enabled": True,
            "workflow": "Release-group repair", "action": "Apply",
            "preview": "release-groups-preview", "command": ["python3", "/config/extended/python/ReleaseGroups.py", "--apply"],
            "description": "Write the release groups shown by the most recent preview.",
        },
        "library-audit": {
            "name": "Audit library mappings", "kind": "preview", "enabled": truthy(config.get("enableLibraryMaintenance")),
            "workflow": "Library mappings", "action": "Audit now",
            "command": ["bash", "/config/extended/LibraryMaintenance.bash", "--audit"],
            "description": "Generate the review-first unmapped-file and optional date audit.",
        },
    }
    tasks["replaygain-maintenance"] = {
        "name": "ReplayGain maintenance", "kind": "write",
        "workflow": "ReplayGain maintenance", "action": "Run now",
        "enabled": truthy(config.get("enableReplaygainTags")) and truthy(config.get("enableReplaygainMaintenance")),
        "command": ["bash", "/config/extended/ReplayGainTagger.bash", "--maintenance", "--force"],
        "description": "Audit and repair missing or partial ReplayGain tags on the configured schedule.",
        "schedule": "replaygain-maintenance",
    }
    musicbrainz = truthy(config.get("enableMusicBrainzHelper"))
    tasks["musicbrainz-genres-scan"] = {
        "name": "Scan library genres", "kind": "write", "section": "musicbrainz",
        "workflow": "Genre contributions", "action": "Scan library", "enabled": musicbrainz,
        "command": ["bash", "/config/extended/MusicBrainzGenres.bash", "--library"],
        "description": "Queue exact MusicBrainz entities and embedded genres from the Lidarr library.",
    }
    tasks["musicbrainz-genres-preview"] = {
        "name": "Preview genre contributions", "kind": "preview", "section": "musicbrainz",
        "workflow": "Genre contributions", "action": "Preview submissions", "enabled": musicbrainz,
        "command": ["bash", "/config/extended/MusicBrainzGenres.bash", "--work", "--preview"],
        "description": "Show queued MusicBrainz genre submissions without sending them.",
    }
    tasks["musicbrainz-genres-submit"] = {
        "name": "Submit genre contributions", "kind": "write", "section": "musicbrainz",
        "workflow": "Genre contributions", "action": "Submit", "enabled": musicbrainz,
        "preview": "musicbrainz-genres-preview",
        "command": ["bash", "/config/extended/MusicBrainzGenres.bash", "--work"],
        "description": "Submit the exact-MBID genre changes shown by the latest preview.",
    }
    tasks["musicbrainz-catalog-scan"] = {
        "name": "Scan missing catalog entries", "kind": "preview", "section": "musicbrainz",
        "workflow": "Missing catalog entries", "action": "Scan next batch",
        "enabled": musicbrainz and truthy(config.get("enableMusicBrainzCatalog")),
        "command": ["python3", "/config/extended/python/MusicBrainzCatalog.py", "--force"],
        "description": "Compare the next monitored Lidarr artists with Apple Music and prepare review-only MusicBrainz editor reports.",
    }
    return tasks


def task_catalog(app, config):
    return lidarr_tasks(config) if app == "lidarr" else {}


def positive_int(value, default):
    try:
        result = int(value)
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default


def available_logs():
    """Return actual log files only; callers never supply filesystem paths."""
    try:
        files = [path for path in LOG_DIR.iterdir()
                 if path.is_file() and path.suffix.casefold() in {".log", ".txt"}]
    except OSError:
        files = []
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return {path.name: path.name for path in files[:200]}


def _pending_jobs(root):
    try:
        return sum(1 for path in Path(root).rglob("*.json") if path.parent.name == "jobs")
    except OSError:
        return 0


def musicbrainz_status(config):
    token_file = MUSICBRAINZ_STATE_DIR / "oauth-tokens.json"
    digest = bool(config.get("musicBrainzUsername") and
                  (config.get("musicBrainzPassword") or config.get("musicBrainzToken")))
    oauth = bool(config.get("musicBrainzOAuthClientId") and token_file.is_file() and token_file.stat().st_size)
    genre_jobs = _pending_jobs(MUSICBRAINZ_STATE_DIR)
    rating_jobs = _pending_jobs(RATINGS_HELPER_STATE_DIR)
    submitted_ratings = 0
    last_rating_submission = 0
    username = config.get("musicBrainzUsername", "")
    if username:
        ledger = RATINGS_HELPER_STATE_DIR / hashlib.sha256(username.encode()).hexdigest()[:16]
        ledgers = list(RATINGS_HELPER_STATE_DIR.glob(f"*/submitted-{ledger.name}.json"))
        if ledgers:
            current = max(ledgers, key=lambda path: path.stat().st_mtime)
            try:
                values = json.loads(current.read_text(encoding="utf-8"))
                submitted_ratings = len(values) if isinstance(values, dict) else 0
                last_rating_submission = current.stat().st_mtime
            except (OSError, ValueError):
                pass
    catalog = musicbrainz_catalog_status(config)
    return {"enabled": truthy(config.get("enableMusicBrainzHelper")),
            "username": username,
            "authenticated": digest or oauth,
            "authentication": "Digest token" if digest else ("OAuth" if oauth else "Not configured"),
            "genre_jobs": genre_jobs, "rating_jobs": rating_jobs,
            "pending_jobs": genre_jobs + rating_jobs,
            "submitted_ratings": submitted_ratings,
            "last_rating_submission": last_rating_submission,
            "catalog": catalog}


def musicbrainz_catalog_status(config):
    try:
        state = json.loads((MUSICBRAINZ_CATALOG_DIR / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    rows = []
    for mbid, record in (state.get("artists") or {}).items():
        if not isinstance(record, dict) or record.get("status") not in {"review", "awaiting-verification", "current", "error"}:
            continue
        rows.append({"mbid": mbid, "artist": record.get("artist", mbid),
                     "status": record.get("status"), "release_seeds": record.get("release_seeds", 0),
                     "apple_releases": record.get("apple_releases", 0),
                     "musicbrainz_releases": record.get("musicbrainz_releases", 0),
                     "scanned_at": record.get("scanned_at", 0), "error": record.get("error", ""),
                     "submitted_at": record.get("submitted_at", 0),
                     "report": bool(record.get("report"))})
    rows.sort(key=lambda row: (row["status"] != "review", -int(row["scanned_at"] or 0)))
    return {"enabled": truthy(config.get("enableMusicBrainzCatalog")),
            "last_run": state.get("last_run", 0), "monitored_artists": state.get("monitored_artists", 0),
            "eligible_artists": state.get("eligible_artists", 0),
            "missing_apple_links": state.get("missing_apple_links", 0),
            "review_count": sum(1 for row in rows if row["status"] == "review"),
            "awaiting_count": sum(1 for row in rows if row["status"] == "awaiting-verification"),
            "artists": rows[:100]}


def mark_catalog_submitted(mbid):
    if not isinstance(mbid, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", mbid):
        raise ValueError("Unknown catalog report")
    state_file = MUSICBRAINZ_CATALOG_DIR / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("Catalog scan state is unavailable") from error
    record = (state.get("artists") or {}).get(mbid)
    if not isinstance(record, dict) or record.get("status") not in {"review", "awaiting-verification"}:
        raise ValueError("This artist has no missing catalog report to mark")
    record["status"] = "awaiting-verification"
    record["submitted_at"] = int(time.time())
    staged = state_file.with_suffix(".json.tmp")
    staged.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, state_file)
    return record


def ratings_status(config):
    providers = [item.strip() for item in config.get("ratingsProvider", "").split(",") if item.strip()]
    if not providers:
        providers = ["lastfm" if config.get("lastfmApiKey") else "musicbrainz"]
    pending = 0
    latest = 0
    try:
        for path in SONICRATINGS_STATE_DIR.rglob("*"):
            if path.is_file():
                latest = max(latest, path.stat().st_mtime)
                if path.parent.name == "jobs" and path.suffix.casefold() == ".json":
                    pending += 1
    except OSError:
        pass
    return {
        "enabled": truthy(config.get("enableRatings")),
        "server_configured": bool(config.get("subsonicUrl") and config.get("subsonicUsername") and config.get("subsonicPassword")),
        "providers": providers,
        "pending_jobs": pending,
        "last_activity": latest,
    }


def _latest_activity(patterns, rules, waiting):
    candidates = []
    for pattern in patterns:
        try:
            candidates.extend(path for path in LOG_DIR.glob(pattern) if path.is_file())
        except OSError:
            pass
    if not candidates:
        return {"detail": waiting, "last_activity": 0}
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    try:
        with latest.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            lines = stream.read().decode("utf-8", "replace").splitlines()
    except OSError:
        lines = []
    for line in reversed(lines):
        for pattern, message in rules:
            match = re.search(pattern, line, re.I)
            if match:
                detail = message(match) if callable(message) else message
                return {"detail": detail, "last_activity": latest.stat().st_mtime}
    return {"detail": waiting, "last_activity": latest.stat().st_mtime}


def overview_status(config):
    services = {
        "soulseek": {"enabled": truthy(config.get("enableSoulseek")), **_latest_activity(
            ("Soulseek.log",),
            ((r"search submitted :: (.+)$", lambda match: "Searching: " + match.group(1)),
             (r"item \d+: handoff$", "Handing a download to Lidarr"),
             (r"item \d+: downloading$", "Download in progress"),
             (r"item \d+: done$", "Download completed"),
             (r"worker failed", "Retry scheduled after a temporary failure")),
            "Waiting for download work")},
        "beets": {"enabled": truthy(config.get("enableBeetsTagging")), **_latest_activity(
            ("BeetsTagger-*.txt",),
            ((r"Finished in .*seconds", "Album enrichment completed"),
             (r"Fixing Tags Complete", "Writing cleaned tags"),
             (r"Begin matching with beets", "Matching an imported album"),
             (r"ERROR", "Last import needs attention")),
            "Waiting for an imported album")},
        "lyrics": {"enabled": truthy(config.get("enableLyrics")), **_latest_activity(
            ("lyrics.log",),
            ((r"embedded lyrics", "Lyrics embedded in a track"),
             (r"missing; cached", "No matching lyrics found; result cached"),
             (r"deferred", "Provider result deferred for safety"),
             (r"scanning for missing lyrics", "Scanning an imported album")),
            "Waiting for lyric work")},
        "replaygain": {"enabled": truthy(config.get("enableReplaygainTags")), **_latest_activity(
            ("ReplayGainTagger-*.txt", "replaygain-maintenance.log"),
            ((r"scanner exited with status 0", "ReplayGain tags written"),
             (r"REPAIR SUMMARY.*failed=0", "ReplayGain maintenance completed"),
             (r"Running ReplayGain scan", "Scanning an imported album"),
             (r"ERROR", "Last ReplayGain run needs attention")),
            "Waiting for ReplayGain work")},
    }
    return {"services": services, "ratings": ratings_status(config),
            "musicbrainz": musicbrainz_status(config)}


def streaming_auth_status(config, tidal_auth=None):
    tidal = tidal_auth.status() if tidal_auth else {"state": "idle", "authorization_url": "", "message": ""}
    tidal["configured"] = (TIDAL_CONFIG_DIR / "token.json").is_file()
    return {"tidal": tidal, "deezer": {"configured": bool(config.get("arlToken"))}}


def save_deezer_arl(arl, path=CONFIG):
    if not isinstance(arl, str) or not re.fullmatch(r"[A-Fa-f0-9]{192}", arl.strip()):
        raise ValueError("Deezer ARL must be the 192-character hexadecimal cookie value")
    arl = arl.strip()
    backup = update_configuration({"arlToken": arl}, path)
    DEEMIX_ARL_FILE.parent.mkdir(parents=True, exist_ok=True)
    staged = DEEMIX_ARL_FILE.with_suffix(".tmp")
    staged.write_text(arl, encoding="ascii")
    os.chmod(staged, 0o600)
    os.replace(staged, DEEMIX_ARL_FILE)
    return backup


class TidalAuth:
    """Keep the interactive PKCE exchange in memory; never persist redirect codes."""
    URL = re.compile(r"https://login\.tidal\.com/authorize\?[^\s<>'\"]+")

    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.state = "idle"
        self.authorization_url = ""
        self.message = ""
        self.output = ""

    def status(self):
        with self.lock:
            return {"state": self.state, "authorization_url": self.authorization_url,
                    "message": self.message}

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError("A TIDAL login is already in progress")
            command = shutil.which("tidal-dl-sg")
            if not command:
                raise ValueError("tidal-dl-sg is not installed")
            env = os.environ.copy()
            env.update(XDG_CONFIG_HOME="/config/extended", PYTHONUNBUFFERED="1")
            self.state, self.authorization_url = "starting", ""
            self.message, self.output = "Starting TIDAL login…", ""
            self.process = subprocess.Popen([command, "login"], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            cwd="/config", env=env, text=True, bufsize=0,
                                            start_new_session=True)
            threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
            return self.status()

    def _read(self, process):
        while True:
            chunk = process.stdout.read(1)
            if not chunk:
                break
            with self.lock:
                self.output = (self.output + chunk)[-32768:]
                match = self.URL.search(self.output)
                if match and not self.authorization_url:
                    self.authorization_url = match.group(0)
                    self.state = "waiting"
                    self.message = "Sign in to TIDAL, then paste the final Oops-page URL below."
        code = process.wait()
        with self.lock:
            if code == 0 and (TIDAL_CONFIG_DIR / "token.json").is_file():
                self.state, self.message = "connected", "TIDAL login completed and the token was saved."
            elif self.state != "cancelled":
                self.state, self.message = "failed", "TIDAL login failed; start it again and use a fresh redirect URL."
            self.process = None

    def complete(self, redirect_url):
        if not isinstance(redirect_url, str) or len(redirect_url) > 4096:
            raise ValueError("Invalid TIDAL redirect URL")
        parsed = urlparse(redirect_url.strip())
        if parsed.scheme != "https" or parsed.netloc.casefold() != "tidal.com" or not parse_qs(parsed.query).get("code"):
            raise ValueError("Paste the complete https://tidal.com Oops-page URL containing the authorization code")
        with self.lock:
            if not self.process or self.process.poll() is not None or self.state != "waiting":
                raise ValueError("Start a TIDAL login before submitting the redirect URL")
            self.state, self.message = "finishing", "Finishing TIDAL login…"
            try:
                self.process.stdin.write(redirect_url.strip() + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise ValueError("The TIDAL login process stopped; start it again") from error
            return self.status()

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
            self.state, self.message = "cancelled", "TIDAL login cancelled."
            self.authorization_url = ""


class JobStore:
    def __init__(self, app, config):
        self.app = app
        self.config = config
        self.tasks = task_catalog(app, config)
        self.path = STATE_DIR / "jobs.json"
        self.lock = threading.RLock()
        self.processes = {}
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        self._reconcile()

    def _load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        staged = self.path.with_suffix(".tmp")
        staged.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(staged, self.path)

    @staticmethod
    def _alive(pid):
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def _reconcile(self):
        changed = False
        for job in self.data.values():
            if job.get("status") == "running":
                if not self._alive(job.get("pid")):
                    job.update(status="finished", ended=time.time(), error="Exit status unavailable after dashboard restart")
                    changed = True
                else:
                    threading.Thread(target=self._watch_detached, args=(job["id"], job["pid"]), daemon=True).start()
        if changed:
            self._save()

    def _watch_detached(self, job_id, pid):
        while self._alive(pid):
            time.sleep(5)
        with self.lock:
            job = self.data.get(job_id, {})
            if job.get("status") == "running":
                job.update(status="finished", ended=time.time(), error="Exit status unavailable after dashboard restart")
                self._save()

    def public_tasks(self):
        cutoff = time.time() - positive_int(self.config.get("dashboardPreviewMaxAgeHours"), 24) * 3600
        result = []
        for task_id, task in self.tasks.items():
            item = {key: task[key] for key in ("name", "kind", "enabled", "description")}
            item["id"] = task_id
            item["workflow"] = task.get("workflow", task["name"])
            item["action"] = task.get("action", "Run now")
            item["section"] = task.get("section", "tasks")
            item["preview"] = task.get("preview")
            preview_time = self._preview_time(task.get("preview")) if task.get("preview") else 0
            item["preview_time"] = preview_time
            item["preview_ready"] = not task.get("preview") or preview_time >= cutoff
            jobs = [job for job in self.data.values() if job.get("task") == task_id]
            latest = max(jobs, key=lambda job: job.get("started", 0), default={})
            item["last_execution"] = latest.get("started", 0)
            item["last_duration"] = ((latest.get("ended") or time.time()) - latest["started"]
                                     if latest.get("started") else None)
            item["last_status"] = latest.get("status")
            item["interval"] = "Manual"
            item["next_execution"] = None
            if task.get("schedule") == "replaygain-maintenance":
                item.update(self._replaygain_schedule())
            result.append(item)
        return result

    @staticmethod
    def _stamp(path):
        try:
            value = int(Path(path).read_text(encoding="utf-8").strip())
            return value if value > 0 else 0
        except (OSError, ValueError):
            return 0

    def _replaygain_schedule(self):
        days = positive_int(self.config.get("replaygainMaintenanceDays"), 30)
        attempt = self._stamp(REPLAYGAIN_STATE_DIR / "maintenance.last-attempt")
        success = self._stamp(REPLAYGAIN_STATE_DIR / "maintenance.last-success")
        successful = bool(attempt and success >= attempt)
        if not truthy(self.config.get("enableReplaygainMaintenance")):
            next_run = None
        elif not attempt:
            next_run = time.time()
        else:
            next_run = (success + days * 86400) if successful else (attempt + 3600)
        return {"interval": f"{days} day" + ("s" if days != 1 else ""),
                "last_execution": attempt, "last_status": "completed" if successful else ("failed" if attempt else None),
                "next_execution": next_run}

    def reload_config(self, config):
        with self.lock:
            self.config = config
            self.tasks = task_catalog(self.app, config)

    def _preview_time(self, task_id):
        return max((job.get("ended", 0) for job in self.data.values()
                    if job.get("task") == task_id and job.get("status") == "completed"), default=0)

    def jobs(self):
        with self.lock:
            return sorted(self.data.values(), key=lambda job: job.get("started", 0), reverse=True)[:50]

    def start(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise KeyError("Unknown task")
            if not task["enabled"]:
                raise ValueError("This task is disabled by extended.conf")
            if any(job.get("task") == task_id and job.get("status") == "running" for job in self.data.values()):
                raise ValueError("This task is already running")
            preview = task.get("preview")
            max_age = positive_int(self.config.get("dashboardPreviewMaxAgeHours"), 24) * 3600
            if preview and self._preview_time(preview) < time.time() - max_age:
                raise ValueError("Run and complete the preview first")
            missing = [part for part in task["command"] if part.startswith("/config/") and not Path(part).exists()]
            if missing:
                raise ValueError("Required script is not installed: " + missing[0])
            job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + task_id + "-" + uuid.uuid4().hex[:6]
            log_path = LOG_DIR / ("dashboard-" + job_id + ".log")
            stream = log_path.open("ab", buffering=0)
            stream.write((f"{time.strftime('%Y-%m-%d %H:%M:%S')} :: START :: {task['name']}\n").encode())
            try:
                process = subprocess.Popen(task["command"], stdout=stream, stderr=subprocess.STDOUT,
                                           cwd="/config", start_new_session=True)
            except Exception:
                stream.close()
                raise
            job = {"id": job_id, "task": task_id, "name": task["name"], "status": "running",
                   "pid": process.pid, "started": time.time(), "ended": None, "exit_code": None,
                   "log": str(log_path)}
            self.data[job_id] = job
            self.processes[job_id] = process
            self._save()
            threading.Thread(target=self._watch, args=(job_id, process, stream), daemon=True).start()
            return job

    def _watch(self, job_id, process, stream):
        code = process.wait()
        stream.write((f"{time.strftime('%Y-%m-%d %H:%M:%S')} :: END :: exit={code}\n").encode())
        stream.close()
        with self.lock:
            job = self.data.get(job_id, {})
            if job.get("status") in {"running", "stopping"}:
                status = "stopped" if job.get("status") == "stopping" else ("completed" if code == 0 else "failed")
                job.update(status=status, ended=time.time(), exit_code=code)
            self.processes.pop(job_id, None)
            self._save()

    def stop(self, job_id):
        with self.lock:
            job = self.data.get(job_id)
            process = self.processes.get(job_id)
            if not job or job.get("status") != "running":
                raise ValueError("Job is not running")
            if not process or process.poll() is not None:
                raise ValueError("Only jobs started by this dashboard session can be stopped")
            os.killpg(process.pid, signal.SIGTERM)
            job.update(status="stopping")
            self._save()


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>arr-scripts</title><style>
:root{color-scheme:dark;--bg:#181818;--side:#242424;--top:#202020;--panel:#202326;--line:#4a4a4a;--text:#eee;--muted:#aeb4b8;--green:#20b978;--amber:#d99425;--red:#e45d5d}
*{box-sizing:border-box}body{margin:0;font:14px system-ui,sans-serif;background:var(--bg);color:var(--text)}button,input,select,textarea{font:inherit}.shell{min-height:100vh;display:grid;grid-template-columns:200px 1fr;grid-template-rows:60px 1fr}.top{grid-column:1/3;background:var(--top);border-bottom:1px solid #151515;display:flex;align-items:center;padding:0 18px;gap:22px}.brand{font-size:18px;font-weight:650}.appname{color:var(--muted)}.donate{margin-left:auto;color:#e84b68;text-decoration:none;font-size:24px;line-height:1;padding:8px;border-radius:4px}.donate:hover,.donate:focus{background:#353535;color:#ff6b84}.updated{margin-top:auto;padding:18px 20px 0;color:var(--muted);font-size:12px}aside{background:var(--side);padding:18px 0;display:flex;flex-direction:column}.nav{display:block;width:100%;border:0;border-left:3px solid transparent;background:transparent;color:var(--text);text-align:left;padding:13px 20px;cursor:pointer}.nav:hover{background:#2c2c2c}.nav.active{color:var(--green);border-left-color:var(--green);background:#2b2b2b}.nav .icon{display:inline-block;width:25px}.content{padding:20px;overflow:auto}.page{display:none}.page.active{display:block}h1{font-size:22px;font-weight:400;margin:0 0 7px}h2{font-size:18px;font-weight:400;margin:28px 0 10px;border-bottom:1px solid #777;padding-bottom:7px}.muted{color:var(--muted)}.notice{position:fixed;right:18px;top:70px;z-index:5;padding:11px 14px;background:#493713;border:1px solid #8e6a21;border-radius:4px;display:none;max-width:520px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px 9px;border-bottom:1px solid var(--line);vertical-align:middle}th{font-weight:600;color:#cfd2d4}tr:hover td{background:#22272a}.actions{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}button,select,input,textarea{background:#30363a;color:var(--text);border:1px solid #555;border-radius:4px;padding:7px 10px}button{cursor:pointer}button.primary{background:#16855b;border-color:#20a970}button.write{background:#805615;border-color:#a97424}button:disabled{opacity:.42;cursor:not-allowed}.completed{color:var(--green)}.failed,.interrupted{color:var(--red)}.running,.stopping{color:var(--amber)}.pill{font-size:12px;border-radius:12px;background:#303a40;padding:2px 7px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);padding:15px;border-radius:5px}.card strong{display:block;margin-bottom:5px}.card .metric{font-size:22px;margin:5px 0}.activity-row+.activity-row{margin-top:10px;padding-top:10px;border-top:1px solid var(--line)}.config-group{border-top:1px solid var(--line);padding:8px 0 14px}.field{display:grid;grid-template-columns:minmax(210px,28%) 1fr;gap:15px;padding:9px 0;align-items:center}.field label{font-weight:600}.field small{display:block;color:var(--muted);font-weight:400;margin-top:3px}.field input{width:100%;max-width:620px}.secret-row{display:flex;gap:7px;max-width:620px}.secret-row input{flex:1}pre{height:calc(100vh - 220px);min-height:360px;overflow:auto;white-space:pre-wrap;background:#0c0e10;border:1px solid var(--line);padding:12px;color:#dce6eb}.editor{width:100%;height:calc(100vh - 260px);min-height:420px;resize:vertical;font:13px ui-monospace,SFMono-Regular,Consolas,monospace;background:#0c0e10;color:#dce6eb}.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}.toolbar select{min-width:280px}.right{margin-left:auto}.empty{padding:25px;color:var(--muted);text-align:center}.mobile-menu{display:none}@media(max-width:760px){.shell{display:block}.top{height:58px;position:sticky;top:0;z-index:4}.mobile-menu{display:inline-block}aside{display:none;position:fixed;z-index:3;top:58px;bottom:0;width:210px}aside.open{display:flex}.content{padding:14px}.field{grid-template-columns:1fr}.hide-mobile{display:none}}
</style></head><body><div class="shell"><header class="top"><button class="mobile-menu" onclick="document.querySelector('aside').classList.toggle('open')">☰</button><div class="brand">arr-scripts</div><div class="appname" id="app"></div><a class="donate" href="https://sickgaming.net/donate" target="_blank" rel="noopener noreferrer" title="Donate to SickGaming" aria-label="Donate to SickGaming">♥</a></header><aside>
<button class="nav active" data-page="overview"><span class="icon">⌂</span>Overview</button>
<button class="nav" data-page="tasks"><span class="icon">◷</span>Tasks</button>
<button class="nav" data-page="streaming"><span class="icon">▶</span>Streaming</button>
<button class="nav" data-page="ratings"><span class="icon">★</span>Ratings</button>
<button class="nav" data-page="musicbrainz"><span class="icon">♫</span>MusicBrainz</button>
<button class="nav" data-page="configuration"><span class="icon">⚙</span>Configuration</button>
<button class="nav" data-page="logs"><span class="icon">▤</span>Logs</button>
<div class="updated" id="updated"></div>
</aside><main class="content"><div id="notice" class="notice"></div>
<section id="overview" class="page active"><h1>Overview</h1><div class="muted">Current Lidarr automation, queues, and recent activity</div><div class="cards" id="overviewCards"></div></section>
<section id="tasks" class="page"><h1>Tasks</h1><div class="muted">Scheduled maintenance and manual library workflows</div><h2>Scheduled</h2><table><thead><tr><th>Name</th><th>Interval</th><th>Last execution</th><th class="hide-mobile">Last duration</th><th>Next execution</th><th></th></tr></thead><tbody id="scheduled"></tbody></table><h2>Manual workflows</h2><table><thead><tr><th>Name</th><th>Last execution</th><th>Status</th><th></th></tr></thead><tbody id="manual"></tbody></table><h2>Queue</h2><table><thead><tr><th>Name</th><th>Status</th><th>Started</th><th>Duration</th><th></th></tr></thead><tbody id="jobs"></tbody></table></section>
<section id="streaming" class="page"><h1>Streaming accounts</h1><div class="muted">Connect the accounts used by Lidarr's streaming download providers</div><div class="cards" id="streamingCards"></div></section>
<section id="ratings" class="page"><h1>Ratings</h1><div class="muted">SonicRatings imports provider ratings into Navidrome or another Subsonic server</div><div class="cards" id="ratingsStatus"></div><h2>Rating workflows</h2><table><tbody id="ratingsTasks"></tbody></table></section>
<section id="musicbrainz" class="page"><h1>MusicBrainz</h1><div class="muted">Contribute metadata from monitored Lidarr music with review before submission</div><div class="cards" id="mbStatus"></div><h2>Contribution workflows</h2><table><tbody id="mbTasks"></tbody></table><h2>Missing catalog entries</h2><div class="cards" id="catalogStatus"></div><table><thead><tr><th>Artist</th><th>Catalog comparison</th><th>Last scan</th><th></th></tr></thead><tbody id="catalogRows"></tbody></table></section>
<section id="configuration" class="page"><h1>Configuration</h1><div class="muted">Edit arr-scripts and Beets configuration. A timestamped backup is created on every save.</div><div class="toolbar"><select id="configFile" onchange="renderConfig()"><option value="extended">extended.conf settings</option><option value="beets">Beets - post-import enrichment</option><option value="genre-whitelist">Beets genre whitelist</option></select><span class="muted" id="configState"></span></div><div id="config"></div><textarea id="configEditor" class="editor" hidden spellcheck="false"></textarea><div class="toolbar"><button class="primary" onclick="saveConfig()">Save changes</button></div></section>
<section id="logs" class="page"><h1>Logs</h1><div class="toolbar"><select id="logSelect"></select><input id="logFilter" placeholder="Filter displayed log" oninput="renderLog()"><button onclick="loadLog()">Refresh</button></div><pre id="log">Select a log.</pre></section>
</main></div><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when=v=>v?new Date(v*1000).toLocaleString():'-';
const relative=v=>{if(!v)return '-';const n=Math.round(v-Date.now()/1000),a=Math.abs(n);const [q,u]=a<90?[Math.max(1,Math.round(a)),'second']:a<5400?[Math.round(a/60),'minute']:a<129600?[Math.round(a/3600),'hour']:[Math.round(a/86400),'day'];return n<=0?(q+' '+u+(q===1?'':'s')+' ago'):('in '+q+' '+u+(q===1?'':'s'))};
const providerName=p=>({musicbrainz:'MusicBrainz',lastfm:'Last.fm',spotify:'Spotify',tidal:'TIDAL',discogs:'Discogs'}[p]||p);
const providerOrder=p=>p.length<2?(p.length?providerName(p[0])+' only':'No provider configured'):providerName(p[0])+' first, then '+p.slice(1).map(providerName).join(', then ');
const duration=v=>v==null?'-':new Date(Math.max(0,v)*1000).toISOString().slice(11,19);
let state={tasks:[],jobs:[],logs:{}},activeJobLog='',rawLog='',configChanges={},configFileContent='';
function note(message){const n=document.querySelector('#notice');n.textContent=message;n.style.display='block';setTimeout(()=>n.style.display='none',7000)}
async function api(path,options){const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw Error(data.error||r.statusText);return data}
function taskButtons(tasks){return `<div class="actions">${tasks.map(t=>`<button class="${t.kind==='write'?'write':'primary'}" title="${esc(!t.enabled?'Disabled in configuration':!t.preview_ready?'Complete the preview first':t.description)}" ${!t.enabled||!t.preview_ready?'disabled':''} onclick="start('${esc(t.id)}')">${esc(!t.enabled?'Disabled':!t.preview_ready?'Preview required':t.action)}</button>`).join('')}</div>`}
function workflows(section){const groups={};state.tasks.filter(t=>t.section===section).forEach(t=>(groups[t.workflow]??=[]).push(t));return groups}
function jobRows(jobs,empty='No dashboard jobs yet.'){return jobs.map(j=>`<tr><td>${esc(j.name)}</td><td class="${esc(j.status)}">${esc(j.status)}</td><td>${when(j.started)}</td><td>${duration((j.ended||Date.now()/1000)-j.started)}</td><td><button onclick="showJob('${esc(j.id)}')">Log</button>${j.status==='running'?` <button onclick="stop('${esc(j.id)}')">Stop</button>`:''}</td></tr>`).join('')||`<tr><td colspan="5" class="empty">${esc(empty)}</td></tr>`}
function renderTasks(){const scheduled=state.tasks.filter(t=>t.section==='tasks'&&t.interval!=='Manual');document.querySelector('#scheduled').innerHTML=scheduled.map(t=>`<tr><td>${esc(t.workflow)}<div class="muted">${esc(t.enabled?'Enabled':'Disabled')}</div></td><td>${esc(t.interval)}</td><td>${when(t.last_execution)}</td><td class="hide-mobile">${duration(t.last_duration)}</td><td>${t.enabled?relative(t.next_execution):'-'}</td><td>${taskButtons([t])}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No scheduled tasks are configured.</td></tr>';const groups=workflows('tasks');document.querySelector('#manual').innerHTML=Object.entries(groups).filter(([,ts])=>!ts.some(t=>t.interval!=='Manual')).map(([name,ts])=>{const last=ts.reduce((a,t)=>t.last_execution>(a?.last_execution||0)?t:a,null);return `<tr><td>${esc(name)}<div class="muted">${esc(ts[0].description)}</div></td><td>${when(last?.last_execution)}</td><td class="${esc(last?.last_status||'')}">${esc(last?.last_status||'Never run')}</td><td>${taskButtons(ts)}</td></tr>`}).join('')||'<tr><td colspan="4" class="empty">No manual workflows available.</td></tr>';document.querySelector('#jobs').innerHTML=state.jobs.map(j=>`<tr><td>${esc(j.name)}</td><td class="${esc(j.status)}">${esc(j.status)}</td><td>${when(j.started)}</td><td>${duration((j.ended||Date.now()/1000)-j.started)}</td><td><button onclick="showJob('${esc(j.id)}')">Log</button>${j.status==='running'?` <button onclick="stop('${esc(j.id)}')">Stop</button>`:''}</td></tr>`).join('')||'<tr><td colspan="5" class="empty">No dashboard jobs yet.</td></tr>'}
const activity=(name,item)=>`<div class="activity-row"><b>${esc(name)}</b> <span class="${item.enabled?'completed':'failed'}">${item.enabled?'On':'Off'}</span><div class="muted">${esc(item.detail)}${item.last_activity?' · '+relative(item.last_activity):''}</div></div>`;
async function renderOverview(){try{const o=await api('/api/overview'),r=o.ratings,m=o.musicbrainz,s=o.services,running=state.jobs.filter(j=>j.status==='running').length,enabled=state.tasks.filter(t=>t.enabled).length;document.querySelector('#overviewCards').innerHTML=`<div class="card"><strong>Dashboard jobs</strong><div class="metric ${running?'running':'completed'}">${running?running+' running':'Idle'}</div><span class="muted">${enabled} available actions</span></div><div class="card"><strong>Downloads</strong>${activity('Soulseek',s.soulseek)}</div><div class="card"><strong>Post-import processing</strong>${activity('Beets',s.beets)}${activity('Lyrics',s.lyrics)}${activity('ReplayGain',s.replaygain)}</div><div class="card"><strong>SonicRatings</strong><div class="metric ${r.enabled?'completed':'failed'}">${r.pending_jobs} waiting</div><span class="muted">Imported albums · ${esc(providerOrder(r.providers))}${r.last_activity?' · '+relative(r.last_activity):''}</span></div><div class="card"><strong>MusicBrainz contributions</strong><div class="metric ${m.enabled&&m.authenticated?'completed':'failed'}">${m.submitted_ratings} ratings submitted</div><span class="muted">${m.genre_jobs} genre jobs · ${m.rating_jobs} rating jobs waiting${m.last_rating_submission?' · last submission '+relative(m.last_rating_submission):''}</span></div>`}catch(e){note(e.message)}}
async function renderStreaming(){try{const s=await api('/api/streaming-auth'),t=s.tidal,d=s.deezer,busy=['starting','waiting','finishing'].includes(t.state);document.querySelector('#streamingCards').innerHTML=`<div class="card"><strong>TIDAL</strong><div class="metric ${t.configured?'completed':'failed'}">${t.configured?'Connected':'Not connected'}</div><p class="muted">${esc(t.message||'Use browser-based PKCE login. No password is stored by arr-scripts.')}</p>${t.authorization_url?`<p><a target="_blank" rel="noopener noreferrer" href="${esc(t.authorization_url)}">Open TIDAL login</a></p><div class="field"><label>Final Oops-page URL<small>After signing in, copy the complete URL from the browser address bar.</small></label><div class="secret-row"><input id="tidalRedirect" type="password" autocomplete="off" placeholder="https://tidal.com/android/login/auth?code=…"><button class="primary" onclick="finishTidal()">Complete login</button></div></div>`:''}<div class="actions" style="justify-content:flex-start"><button class="primary" ${busy?'disabled':''} onclick="startTidal()">${t.configured?'Reconnect TIDAL':'Connect TIDAL'}</button>${busy?'<button onclick="cancelTidal()">Cancel</button>':''}</div></div><div class="card"><strong>Deezer</strong><div class="metric ${d.configured?'completed':'failed'}">${d.configured?'ARL configured':'Not connected'}</div><p class="muted">Deezer does not provide this downloader with a browser OAuth callback. Sign in to Deezer, copy the ARL cookie value, and save it here.</p><p><a target="_blank" rel="noopener noreferrer" href="https://www.deezer.com/login">Open Deezer login</a></p><div class="field"><label>ARL cookie<small>Stored in extended.conf and Deemix with restricted file permissions.</small></label><div class="secret-row"><input id="deezerArl" type="password" autocomplete="off" maxlength="192" placeholder="192-character ARL"><button class="primary" onclick="saveDeezer()">Save ARL</button></div></div></div>`}catch(e){note(e.message)}}
async function startTidal(){try{const d=await api('/api/streaming-auth/tidal/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});note(d.message||'TIDAL login started');await pollTidal()}catch(e){note(e.message)}}
async function pollTidal(){for(let i=0;i<30;i++){await new Promise(r=>setTimeout(r,1000));await renderStreaming();const s=await api('/api/streaming-auth');if(!['starting','finishing'].includes(s.tidal.state))break}}
async function finishTidal(){const value=document.querySelector('#tidalRedirect')?.value||'';try{const d=await api('/api/streaming-auth/tidal/complete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({redirect_url:value})});note(d.message);await pollTidal()}catch(e){note(e.message)}}
async function cancelTidal(){try{await api('/api/streaming-auth/tidal/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await renderStreaming()}catch(e){note(e.message)}}
async function saveDeezer(){const value=document.querySelector('#deezerArl')?.value||'';try{await api('/api/streaming-auth/deezer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({arl:value})});note('Deezer ARL saved');await renderStreaming()}catch(e){note(e.message)}}
async function renderRatings(){try{const r=await api('/api/ratings');document.querySelector('#ratingsStatus').innerHTML=`<div class="card"><strong>Automation</strong><span class="${r.enabled?'completed':'failed'}">${r.enabled?'Enabled':'Disabled'}</span></div><div class="card"><strong>Subsonic server</strong><span class="${r.server_configured?'completed':'failed'}">${r.server_configured?'Configured':'Not configured'}</span></div><div class="card"><strong>Fallback order</strong>${esc(providerOrder(r.providers))}</div><div class="card"><strong>Pending imported albums</strong><span class="metric">${r.pending_jobs}</span></div><div class="card"><strong>Last activity</strong>${when(r.last_activity)}</div>`;document.querySelector('#ratingsTasks').innerHTML=Object.entries(workflows('ratings')).map(([name,ts])=>`<tr><td>${esc(name)}<div class="muted">${esc(ts[0].description)}</div></td><td>${taskButtons(ts)}</td></tr>`).join('')||'<tr><td class="empty">No rating workflows available.</td></tr>'}catch(e){note(e.message)}}
async function renderMusicBrainz(){try{const m=await api('/api/musicbrainz'),c=m.catalog;document.querySelector('#mbStatus').innerHTML=`<div class="card"><strong>Helper</strong><span class="${m.enabled?'completed':'failed'}">${m.enabled?'Enabled':'Disabled'}</span></div><div class="card"><strong>Account</strong>${esc(m.username||'Not configured')}</div><div class="card"><strong>Authentication</strong><span class="${m.authenticated?'completed':'failed'}">${esc(m.authentication)}</span></div><div class="card"><strong>Submitted recording ratings</strong><span class="metric">${m.submitted_ratings}</span><span class="muted">${m.last_rating_submission?'Last submission '+relative(m.last_rating_submission):'No submissions recorded'}</span></div><div class="card"><strong>Pending genre jobs</strong><span class="metric">${m.genre_jobs}</span></div><div class="card"><strong>Pending rating jobs</strong><span class="metric">${m.rating_jobs}</span></div>`;document.querySelector('#mbTasks').innerHTML=Object.entries(workflows('musicbrainz')).map(([name,ts])=>`<tr><td>${esc(name)}<div class="muted">${esc(ts[0].description)}</div></td><td>${taskButtons(ts)}</td></tr>`).join('');document.querySelector('#catalogStatus').innerHTML=`<div class="card"><strong>Catalog scan</strong><span class="${c.enabled?'completed':'failed'}">${c.enabled?'Enabled':'Disabled'}</span><div class="muted">${c.last_run?'Last run '+relative(c.last_run):'Not run yet'}</div></div><div class="card"><strong>Ready for review</strong><span class="metric">${c.review_count}</span><span class="muted">${c.awaiting_count} awaiting verification</span></div><div class="card"><strong>Eligible monitored artists</strong><span class="metric">${c.eligible_artists}</span><span class="muted">${c.missing_apple_links} without Apple Music links</span></div>`;document.querySelector('#catalogRows').innerHTML=c.artists.map(r=>`<tr><td>${esc(r.artist)}<div class="muted">${esc(r.mbid)}</div></td><td><span class="${r.status==='error'?'failed':r.status==='review'?'running':'completed'}">${r.status==='review'?r.release_seeds+' possible missing':r.status==='awaiting-verification'?'Awaiting MusicBrainz verification':r.status==='current'?'No missing releases':'Scan failed'}</span><div class="muted">${r.apple_releases} Apple Music · ${r.musicbrainz_releases} exact MusicBrainz${r.submitted_at?' · marked submitted '+relative(r.submitted_at):''}${r.error?' · '+esc(r.error):''}</div></td><td>${when(r.scanned_at)}</td><td>${r.report?`<a class="button" target="_blank" rel="noopener noreferrer" href="/api/musicbrainz-catalog/report?id=${encodeURIComponent(r.mbid)}">Review</a>`:''}${r.status==='review'?` <button onclick="markCatalogSubmitted('${esc(r.mbid)}')">Mark submitted</button>`:''}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">No artists have been scanned yet. Enable catalog review, then scan the first batch.</td></tr>'}catch(e){note(e.message)}}
async function markCatalogSubmitted(mbid){try{await api('/api/musicbrainz-catalog/submitted',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mbid})});note('Marked submitted; the next scan will verify MusicBrainz.');await renderMusicBrainz()}catch(e){note(e.message)}}
async function renderConfig(){try{const selected=document.querySelector('#configFile').value,form=document.querySelector('#config'),editor=document.querySelector('#configEditor');configChanges={};document.querySelector('#configState').textContent='';if(selected==='extended'){const d=await api('/api/config');editor.hidden=true;form.hidden=false;form.innerHTML=d.groups.map(g=>`<div class="config-group"><h2>${esc(g.name)}</h2>${g.fields.map(f=>`<div class="field"><label>${esc(f.name)}<small>${esc(f.description)}</small></label>${f.secret?`<div class="secret-row"><input type="password" data-key="${esc(f.name)}" placeholder="${f.configured?'Configured — enter to replace':'Not configured'}"><button onclick="clearSecret('${esc(f.name)}')">Clear</button></div>`:`<input data-key="${esc(f.name)}" value="${esc(f.value)}">`}</div>`).join('')}</div>`).join('');document.querySelectorAll('[data-key]').forEach(e=>e.addEventListener('input',()=>{configChanges[e.dataset.key]=e.value;document.querySelector('#configState').textContent='Unsaved changes'}))}else{const d=await api('/api/config-file?name='+encodeURIComponent(selected));form.hidden=true;editor.hidden=false;editor.value=d.content;configFileContent=d.content;editor.oninput=()=>document.querySelector('#configState').textContent=editor.value===configFileContent?'':'Unsaved changes'}}catch(e){note(e.message)}}
function clearSecret(key){configChanges[key]='';const e=document.querySelector(`[data-key="${CSS.escape(key)}"]`);if(e)e.value='';document.querySelector('#configState').textContent='Secret will be cleared'}
async function saveConfig(){const selected=document.querySelector('#configFile').value;try{let d;if(selected==='extended'){if(!Object.keys(configChanges).length)return note('No configuration changes');d=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({changes:configChanges})})}else{const content=document.querySelector('#configEditor').value;if(content===configFileContent)return note('No configuration changes');d=await api('/api/config-file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:selected,content})})}note('Configuration saved; backup: '+d.backup);await renderConfig();await refresh()}catch(e){note(e.message)}}
async function start(id){try{await api('/api/jobs/'+encodeURIComponent(id),{method:'POST'});await refresh()}catch(e){note(e.message)}}
async function stop(id){try{await api('/api/jobs/'+encodeURIComponent(id)+'/stop',{method:'POST'});await refresh()}catch(e){note(e.message)}}
async function refresh(){try{state=await api('/api/status');document.querySelector('#app').textContent=state.app.toUpperCase();document.querySelector('#updated').textContent='Updated '+new Date().toLocaleTimeString();renderTasks();const s=document.querySelector('#logSelect'),old=s.value;s.innerHTML=Object.entries(state.logs).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');if([...s.options].some(o=>o.value===old))s.value=old;if(document.querySelector('#overview').classList.contains('active'))renderOverview();if(document.querySelector('#streaming').classList.contains('active'))renderStreaming();if(document.querySelector('#ratings').classList.contains('active'))renderRatings();if(document.querySelector('#musicbrainz').classList.contains('active'))renderMusicBrainz()}catch(e){note(e.message)}}
async function loadLog(){try{activeJobLog='';const id=document.querySelector('#logSelect').value;if(!id)return;rawLog=(await api('/api/log?name='+encodeURIComponent(id)+'&lines=500')).content||'Log is empty or has not been created.';renderLog()}catch(e){note(e.message)}}
function renderLog(){const q=document.querySelector('#logFilter').value.toLowerCase();const text=q?rawLog.split('\n').filter(line=>line.toLowerCase().includes(q)).join('\n'):rawLog;const p=document.querySelector('#log');p.textContent=text;p.scrollTop=p.scrollHeight}
async function showJob(id){showPage('logs');try{activeJobLog=id;rawLog=(await api('/api/job-log?id='+encodeURIComponent(id)+'&lines=500')).content||'Log is empty.';renderLog()}catch(e){note(e.message)}}
function showPage(page){if(!document.getElementById(page))page='overview';document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id===page));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===page));location.hash=page;document.querySelector('aside').classList.remove('open');if(page==='overview')renderOverview();if(page==='streaming')renderStreaming();if(page==='ratings')renderRatings();if(page==='configuration')renderConfig();if(page==='musicbrainz')renderMusicBrainz();if(page==='logs'&&!rawLog)loadLog()}
document.querySelectorAll('.nav').forEach(e=>e.onclick=()=>showPage(e.dataset.page));showPage(location.hash.slice(1)||'overview');refresh();setInterval(()=>{refresh();if(document.querySelector('#logs').classList.contains('active')){if(activeJobLog)showJob(activeJobLog);else if(document.querySelector('#logSelect').value)loadLog()}},5000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = "arr-scripts-dashboard/0.1"

    def log_message(self, form, *args):
        with (LOG_DIR / "dashboard.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} :: {self.address_string()} :: {form % args}\n")

    def _authorized(self):
        username = self.server.config.get("dashboardUsername", "")
        password = self.server.config.get("dashboardPassword", "")
        if not username and not password:
            return True
        supplied = self.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        return hmac.compare_digest(supplied, expected)

    def _auth(self):
        if self._authorized():
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="arr-scripts"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _send(self, value, status=HTTPStatus.OK, content_type="application/json", csp=None):
        body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", csp or "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Use application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length") from None
        if not 0 < length <= 65536:
            raise ValueError("Request body must be between 1 byte and 64 KiB")
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            raise ValueError("Invalid JSON") from None
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    @staticmethod
    def _tail(path, count):
        try:
            with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
                return "".join(stream.readlines()[-count:])
        except OSError:
            return ""

    def do_GET(self):
        if not self._auth(): return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            return self._send(HTML, content_type="text/html")
        if parsed.path == "/api/status":
            return self._send({"app": self.server.app, "tasks": self.server.store.public_tasks(),
                               "jobs": self.server.store.jobs(), "logs": available_logs()})
        if parsed.path == "/api/config":
            return self._send({"groups": configuration_fields()})
        if parsed.path == "/api/config-file":
            try:
                return self._send(auxiliary_configuration(query.get("name", [""])[0]))
            except ValueError as error:
                return self._send({"error": str(error)}, HTTPStatus.NOT_FOUND)
        if parsed.path == "/api/ratings":
            return self._send(ratings_status(self.server.config))
        if parsed.path == "/api/musicbrainz":
            return self._send(musicbrainz_status(self.server.config))
        if parsed.path == "/api/musicbrainz-catalog/report":
            mbid = query.get("id", [""])[0]
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", mbid):
                return self._send({"error": "Unknown report"}, HTTPStatus.NOT_FOUND)
            try:
                return self._send((MUSICBRAINZ_CATALOG_DIR / (mbid + ".html")).read_text(encoding="utf-8"),
                                  content_type="text/html",
                                  csp="default-src 'self'; style-src 'self' 'unsafe-inline'; img-src https: data:; form-action https://musicbrainz.org")
            except OSError:
                return self._send({"error": "Report unavailable"}, HTTPStatus.NOT_FOUND)
        if parsed.path == "/api/overview":
            return self._send(overview_status(self.server.config))
        if parsed.path == "/api/streaming-auth":
            return self._send(streaming_auth_status(self.server.config, self.server.tidal_auth))
        if parsed.path == "/api/log":
            name = query.get("name", [""])[0]
            filename = available_logs().get(name)
            if not filename: return self._send({"error": "Unknown log"}, HTTPStatus.NOT_FOUND)
            count = min(1000, max(1, int(query.get("lines", ["300"])[0])))
            return self._send({"content": self._tail(LOG_DIR / filename, count)})
        if parsed.path == "/api/job-log":
            job_id = query.get("id", [""])[0]
            job = next((item for item in self.server.store.jobs() if item["id"] == job_id), None)
            if not job: return self._send({"error": "Unknown job"}, HTTPStatus.NOT_FOUND)
            count = min(1000, max(1, int(query.get("lines", ["300"])[0])))
            return self._send({"content": self._tail(job["log"], count)})
        return self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self._auth(): return
        path = urlparse(self.path).path
        if path == "/api/config":
            try:
                body = self._json_body()
                backup = update_configuration(body.get("changes"))
                config = read_config()
                self.server.config = config
                self.server.store.reload_config(config)
                return self._send({"saved": True, "backup": backup.name})
            except (OSError, ValueError) as error:
                return self._send({"error": str(error)}, HTTPStatus.CONFLICT)
        if path == "/api/config-file":
            try:
                body = self._json_body()
                backup = update_auxiliary_configuration(body.get("name"), body.get("content"))
                return self._send({"saved": True, "backup": backup.name})
            except (OSError, ValueError) as error:
                return self._send({"error": str(error)}, HTTPStatus.CONFLICT)
        if path == "/api/streaming-auth/tidal/start":
            try:
                return self._send(self.server.tidal_auth.start(), HTTPStatus.ACCEPTED)
            except (OSError, ValueError) as error:
                return self._send({"error": str(error)}, HTTPStatus.CONFLICT)
        if path == "/api/streaming-auth/tidal/complete":
            try:
                return self._send(self.server.tidal_auth.complete(self._json_body().get("redirect_url")),
                                  HTTPStatus.ACCEPTED)
            except (OSError, ValueError) as error:
                return self._send({"error": str(error)}, HTTPStatus.CONFLICT)
        if path == "/api/streaming-auth/tidal/cancel":
            self.server.tidal_auth.stop()
            return self._send(self.server.tidal_auth.status())
        if path == "/api/streaming-auth/deezer":
            try:
                backup = save_deezer_arl(self._json_body().get("arl"))
                config = read_config()
                self.server.config = config
                self.server.store.reload_config(config)
                return self._send({"saved": True, "backup": backup.name})
            except (OSError, ValueError) as error:
                return self._send({"error": str(error)}, HTTPStatus.CONFLICT)
        if path == "/api/musicbrainz-catalog/submitted":
            try:
                record = mark_catalog_submitted(self._json_body().get("mbid"))
                return self._send({"saved": True, "submitted_at": record["submitted_at"]})
            except (OSError, ValueError) as error:
                return self._send({"error": str(error)}, HTTPStatus.CONFLICT)
        match = re.fullmatch(r"/api/jobs/([a-z0-9-]+)(/stop)?", path)
        if not match: return self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        try:
            if match.group(2):
                self.server.store.stop(match.group(1)); result = {"status": "stopping"}
            else:
                result = self.server.store.start(match.group(1))
            return self._send(result, HTTPStatus.ACCEPTED)
        except KeyError as error:
            return self._send({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except (OSError, ValueError) as error:
            return self._send({"error": str(error)}, HTTPStatus.CONFLICT)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=("lidarr", "sonarr", "radarr", "readarr"), required=True)
    args = parser.parse_args(argv)
    config = read_config()
    bind = config.get("dashboardBindAddress", "0.0.0.0")
    username, password = config.get("dashboardUsername", ""), config.get("dashboardPassword", "")
    if bool(username) != bool(password):
        raise SystemExit("Set both dashboardUsername and dashboardPassword, or leave both blank")
    try:
        port = int(config.get("dashboardPort", str(DEFAULT_PORTS[args.app])))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("dashboardPort must be between 1 and 65535") from None
    server = ThreadingHTTPServer((bind, port), Handler)
    server.app, server.config, server.store = args.app, config, JobStore(args.app, config)
    server.tidal_auth = TidalAuth()
    print(f"arr-scripts dashboard: http://{bind}:{port} ({args.app})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
