#!/usr/bin/env python3
"""Small, dependency-free web dashboard for approved arr-scripts commands."""

import argparse
import base64
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
CONFIG_FILES = {
    "beets-download": (Path("/config/extended/beets-config.yaml"), "Beets - download processing"),
    "beets-lidarr": (Path("/config/extended/beets-config-lidarr.yaml"), "Beets - Lidarr import hook"),
    "genre-whitelist": (Path("/config/extended/beets-genre-whitelist.txt"), "Beets genre whitelist"),
}
SECRET_NAME = re.compile(r"(?:password|token|secret|api_?key|arl$)", re.I)
CONFIG_GROUPS = (
    ("Dashboard", ("enableDashboard", "dashboardBindAddress", "dashboardPort", "dashboardUsername", "dashboardPassword", "dashboardPreviewMaxAgeHours")),
    ("ReplayGain", ("enableReplaygainTags", "enableReplaygainMaintenance", "replaygainMaintenanceDays", "replaygainTargetLoudness", "replaygainThreads", "replaygainPreserveMtime", "replaygainClipMode", "replaygainTruePeak", "replaygainMaxPeak")),
    ("Lyrics", ("enableLyrics", "enableLyricsMaintenance", "lyricsMaintenanceDays", "lyricsLibraryRoot", "lyricsTimeout", "lyricsUrl")),
    ("MusicBrainz", ("enableMusicBrainzHelper", "musicBrainzUsername", "musicBrainzPassword", "musicBrainzToken", "musicBrainzOAuthClientId", "musicBrainzOAuthClientSecret", "musicBrainzOAuthRedirectUri")),
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
    backup = path.with_name(path.name + ".dashboard-" + time.strftime("%Y%m%d-%H%M%S") + ".bak")
    shutil.copy2(path, backup)
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
    backup = path.with_name(path.name + ".dashboard-" + time.strftime("%Y%m%d-%H%M%S") + ".bak")
    shutil.copy2(path, backup)
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


def musicbrainz_status(config):
    token_file = Path("/config/extended/musicbrainz-helper/oauth-tokens.json")
    digest = bool(config.get("musicBrainzUsername") and
                  (config.get("musicBrainzPassword") or config.get("musicBrainzToken")))
    oauth = bool(config.get("musicBrainzOAuthClientId") and token_file.is_file() and token_file.stat().st_size)
    queue_roots = (Path("/config/extended/musicbrainz-helper"), Path("/config/extended/ratings-helper"))
    pending = 0
    for root in queue_roots:
        try:
            pending += sum(1 for path in root.rglob("*.json") if path.name != "oauth-tokens.json")
        except OSError:
            pass
    return {"enabled": truthy(config.get("enableMusicBrainzHelper")),
            "username": config.get("musicBrainzUsername", ""),
            "authenticated": digest or oauth,
            "authentication": "Digest token" if digest else ("OAuth" if oauth else "Not configured"),
            "queued_files": pending}


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
        days = positive_int(self.config.get("replaygainMaintenanceDays"), 7)
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
*{box-sizing:border-box}body{margin:0;font:14px system-ui,sans-serif;background:var(--bg);color:var(--text)}button,input,select,textarea{font:inherit}.shell{min-height:100vh;display:grid;grid-template-columns:200px 1fr;grid-template-rows:60px 1fr}.top{grid-column:1/3;background:var(--top);border-bottom:1px solid #151515;display:flex;align-items:center;padding:0 18px;gap:22px}.brand{font-size:18px;font-weight:650}.appname{color:var(--muted)}.donate{margin-left:auto;color:#e84b68;text-decoration:none;font-size:24px;line-height:1;padding:8px;border-radius:4px}.donate:hover,.donate:focus{background:#353535;color:#ff6b84}.updated{margin-top:auto;padding:18px 20px 0;color:var(--muted);font-size:12px}aside{background:var(--side);padding:18px 0;display:flex;flex-direction:column}.nav{display:block;width:100%;border:0;border-left:3px solid transparent;background:transparent;color:var(--text);text-align:left;padding:13px 20px;cursor:pointer}.nav:hover{background:#2c2c2c}.nav.active{color:var(--green);border-left-color:var(--green);background:#2b2b2b}.nav .icon{display:inline-block;width:25px}.content{padding:20px;overflow:auto}.page{display:none}.page.active{display:block}h1{font-size:22px;font-weight:400;margin:0 0 7px}h2{font-size:18px;font-weight:400;margin:28px 0 10px;border-bottom:1px solid #777;padding-bottom:7px}.muted{color:var(--muted)}.notice{position:fixed;right:18px;top:70px;z-index:5;padding:11px 14px;background:#493713;border:1px solid #8e6a21;border-radius:4px;display:none;max-width:520px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px 9px;border-bottom:1px solid var(--line);vertical-align:middle}th{font-weight:600;color:#cfd2d4}tr:hover td{background:#22272a}.actions{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}button,select,input,textarea{background:#30363a;color:var(--text);border:1px solid #555;border-radius:4px;padding:7px 10px}button{cursor:pointer}button.primary{background:#16855b;border-color:#20a970}button.write{background:#805615;border-color:#a97424}button:disabled{opacity:.42;cursor:not-allowed}.completed{color:var(--green)}.failed,.interrupted{color:var(--red)}.running,.stopping{color:var(--amber)}.pill{font-size:12px;border-radius:12px;background:#303a40;padding:2px 7px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);padding:15px;border-radius:5px}.card strong{display:block;margin-bottom:5px}.card .metric{font-size:22px;margin:5px 0}.config-group{border-top:1px solid var(--line);padding:8px 0 14px}.field{display:grid;grid-template-columns:minmax(210px,28%) 1fr;gap:15px;padding:9px 0;align-items:center}.field label{font-weight:600}.field small{display:block;color:var(--muted);font-weight:400;margin-top:3px}.field input{width:100%;max-width:620px}.secret-row{display:flex;gap:7px;max-width:620px}.secret-row input{flex:1}pre{height:calc(100vh - 220px);min-height:360px;overflow:auto;white-space:pre-wrap;background:#0c0e10;border:1px solid var(--line);padding:12px;color:#dce6eb}.editor{width:100%;height:calc(100vh - 260px);min-height:420px;resize:vertical;font:13px ui-monospace,SFMono-Regular,Consolas,monospace;background:#0c0e10;color:#dce6eb}.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}.toolbar select{min-width:280px}.right{margin-left:auto}.empty{padding:25px;color:var(--muted);text-align:center}.mobile-menu{display:none}@media(max-width:760px){.shell{display:block}.top{height:58px;position:sticky;top:0;z-index:4}.mobile-menu{display:inline-block}aside{display:none;position:fixed;z-index:3;top:58px;bottom:0;width:210px}aside.open{display:flex}.content{padding:14px}.field{grid-template-columns:1fr}.hide-mobile{display:none}}
</style></head><body><div class="shell"><header class="top"><button class="mobile-menu" onclick="document.querySelector('aside').classList.toggle('open')">☰</button><div class="brand">arr-scripts</div><div class="appname" id="app"></div><a class="donate" href="https://sickgaming.net/donate" target="_blank" rel="noopener noreferrer" title="Donate to SickGaming" aria-label="Donate to SickGaming">♥</a></header><aside>
<button class="nav active" data-page="overview"><span class="icon">⌂</span>Overview</button>
<button class="nav" data-page="tasks"><span class="icon">◷</span>Tasks</button>
<button class="nav" data-page="ratings"><span class="icon">★</span>Ratings</button>
<button class="nav" data-page="musicbrainz"><span class="icon">♫</span>MusicBrainz</button>
<button class="nav" data-page="configuration"><span class="icon">⚙</span>Configuration</button>
<button class="nav" data-page="logs"><span class="icon">▤</span>Logs</button>
<div class="updated" id="updated"></div>
</aside><main class="content"><div id="notice" class="notice"></div>
<section id="overview" class="page active"><h1>Overview</h1><div class="muted">Current Lidarr automation, queues, and recent activity</div><div class="cards" id="overviewCards"></div><h2>Active and recent work</h2><table><thead><tr><th>Name</th><th>Status</th><th>Started</th><th>Duration</th><th></th></tr></thead><tbody id="overviewJobs"></tbody></table></section>
<section id="tasks" class="page"><h1>Tasks</h1><div class="muted">Scheduled maintenance and manual library workflows</div><h2>Scheduled</h2><table><thead><tr><th>Name</th><th>Interval</th><th>Last execution</th><th class="hide-mobile">Last duration</th><th>Next execution</th><th></th></tr></thead><tbody id="scheduled"></tbody></table><h2>Manual workflows</h2><table><thead><tr><th>Name</th><th>Last execution</th><th>Status</th><th></th></tr></thead><tbody id="manual"></tbody></table><h2>Queue</h2><table><thead><tr><th>Name</th><th>Status</th><th>Started</th><th>Duration</th><th></th></tr></thead><tbody id="jobs"></tbody></table></section>
<section id="ratings" class="page"><h1>Ratings</h1><div class="muted">SonicRatings imports provider ratings into Navidrome or another Subsonic server</div><div class="cards" id="ratingsStatus"></div><h2>Rating workflows</h2><table><tbody id="ratingsTasks"></tbody></table></section>
<section id="musicbrainz" class="page"><h1>MusicBrainz</h1><div class="muted">Contribute metadata from monitored Lidarr music with review before submission</div><div class="cards" id="mbStatus"></div><h2>Contribution workflows</h2><table><tbody id="mbTasks"></tbody></table><h2>Missing catalog entries</h2><div class="card"><strong>Monitored artist and release review queue</strong><p class="muted">The dashboard will compare Lidarr’s monitored catalog with MusicBrainz and prepare seeded editor links here. Full artist and release additions always require review in MusicBrainz.</p><span class="pill">Backend pending</span></div></section>
<section id="configuration" class="page"><h1>Configuration</h1><div class="muted">Edit arr-scripts and Beets configuration. A timestamped backup is created on every save.</div><div class="toolbar"><select id="configFile" onchange="renderConfig()"><option value="extended">extended.conf settings</option><option value="beets-download">Beets - download processing</option><option value="beets-lidarr">Beets - Lidarr import hook</option><option value="genre-whitelist">Beets genre whitelist</option></select><span class="muted" id="configState"></span></div><div id="config"></div><textarea id="configEditor" class="editor" hidden spellcheck="false"></textarea><div class="toolbar"><button class="primary" onclick="saveConfig()">Save changes</button></div></section>
<section id="logs" class="page"><h1>Logs</h1><div class="toolbar"><select id="logSelect"></select><input id="logFilter" placeholder="Filter displayed log" oninput="renderLog()"><button onclick="loadLog()">Refresh</button></div><pre id="log">Select a log.</pre></section>
</main></div><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when=v=>v?new Date(v*1000).toLocaleString():'-';
const relative=v=>{if(!v)return '-';const n=Math.round(v-Date.now()/1000),a=Math.abs(n);const [q,u]=a<90?[Math.max(1,Math.round(a)),'second']:a<5400?[Math.round(a/60),'minute']:a<129600?[Math.round(a/3600),'hour']:[Math.round(a/86400),'day'];return n<=0?(q+' '+u+(q===1?'':'s')+' ago'):('in '+q+' '+u+(q===1?'':'s'))};
const duration=v=>v==null?'-':new Date(Math.max(0,v)*1000).toISOString().slice(11,19);
let state={tasks:[],jobs:[],logs:{}},activeJobLog='',rawLog='',configChanges={},configFileContent='';
function note(message){const n=document.querySelector('#notice');n.textContent=message;n.style.display='block';setTimeout(()=>n.style.display='none',7000)}
async function api(path,options){const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw Error(data.error||r.statusText);return data}
function taskButtons(tasks){return `<div class="actions">${tasks.map(t=>`<button class="${t.kind==='write'?'write':'primary'}" title="${esc(!t.enabled?'Disabled in configuration':!t.preview_ready?'Complete the preview first':t.description)}" ${!t.enabled||!t.preview_ready?'disabled':''} onclick="start('${esc(t.id)}')">${esc(!t.enabled?'Disabled':!t.preview_ready?'Preview required':t.action)}</button>`).join('')}</div>`}
function workflows(section){const groups={};state.tasks.filter(t=>t.section===section).forEach(t=>(groups[t.workflow]??=[]).push(t));return groups}
function jobRows(jobs,empty='No dashboard jobs yet.'){return jobs.map(j=>`<tr><td>${esc(j.name)}</td><td class="${esc(j.status)}">${esc(j.status)}</td><td>${when(j.started)}</td><td>${duration((j.ended||Date.now()/1000)-j.started)}</td><td><button onclick="showJob('${esc(j.id)}')">Log</button>${j.status==='running'?` <button onclick="stop('${esc(j.id)}')">Stop</button>`:''}</td></tr>`).join('')||`<tr><td colspan="5" class="empty">${esc(empty)}</td></tr>`}
function renderTasks(){const scheduled=state.tasks.filter(t=>t.section==='tasks'&&t.interval!=='Manual');document.querySelector('#scheduled').innerHTML=scheduled.map(t=>`<tr><td>${esc(t.workflow)}<div class="muted">${esc(t.enabled?'Enabled':'Disabled')}</div></td><td>${esc(t.interval)}</td><td>${when(t.last_execution)}</td><td class="hide-mobile">${duration(t.last_duration)}</td><td>${t.enabled?relative(t.next_execution):'-'}</td><td>${taskButtons([t])}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No scheduled tasks are configured.</td></tr>';const groups=workflows('tasks');document.querySelector('#manual').innerHTML=Object.entries(groups).filter(([,ts])=>!ts.some(t=>t.interval!=='Manual')).map(([name,ts])=>{const last=ts.reduce((a,t)=>t.last_execution>(a?.last_execution||0)?t:a,null);return `<tr><td>${esc(name)}<div class="muted">${esc(ts[0].description)}</div></td><td>${when(last?.last_execution)}</td><td class="${esc(last?.last_status||'')}">${esc(last?.last_status||'Never run')}</td><td>${taskButtons(ts)}</td></tr>`}).join('')||'<tr><td colspan="4" class="empty">No manual workflows available.</td></tr>';document.querySelector('#jobs').innerHTML=state.jobs.map(j=>`<tr><td>${esc(j.name)}</td><td class="${esc(j.status)}">${esc(j.status)}</td><td>${when(j.started)}</td><td>${duration((j.ended||Date.now()/1000)-j.started)}</td><td><button onclick="showJob('${esc(j.id)}')">Log</button>${j.status==='running'?` <button onclick="stop('${esc(j.id)}')">Stop</button>`:''}</td></tr>`).join('')||'<tr><td colspan="5" class="empty">No dashboard jobs yet.</td></tr>'}
async function renderOverview(){try{const [r,m]=await Promise.all([api('/api/ratings'),api('/api/musicbrainz')]);const running=state.jobs.filter(j=>j.status==='running').length;const enabled=state.tasks.filter(t=>t.enabled).length;document.querySelector('#overviewCards').innerHTML=`<div class="card"><strong>Automation</strong><div class="metric ${running?'running':'completed'}">${running?running+' running':'Idle'}</div><span class="muted">${enabled} dashboard actions enabled</span></div><div class="card"><strong>SonicRatings</strong><div class="metric ${r.enabled?'completed':'failed'}">${r.enabled?'Enabled':'Disabled'}</div><span class="muted">${esc(r.providers.join(' → '))} · ${r.pending_jobs} queued</span></div><div class="card"><strong>MusicBrainz helper</strong><div class="metric ${m.enabled&&m.authenticated?'completed':'failed'}">${m.enabled?(m.authenticated?'Ready':'Needs authentication'):'Disabled'}</div><span class="muted">${m.queued_files} contribution files queued</span></div><div class="card"><strong>Recent activity</strong><div class="metric">${r.last_activity?relative(r.last_activity):'None yet'}</div><span class="muted">Latest SonicRatings state update</span></div>`;document.querySelector('#overviewJobs').innerHTML=jobRows(state.jobs.slice(0,10),'No dashboard jobs have run yet.')}catch(e){note(e.message)}}
async function renderRatings(){try{const r=await api('/api/ratings');document.querySelector('#ratingsStatus').innerHTML=`<div class="card"><strong>Automation</strong><span class="${r.enabled?'completed':'failed'}">${r.enabled?'Enabled':'Disabled'}</span></div><div class="card"><strong>Subsonic server</strong><span class="${r.server_configured?'completed':'failed'}">${r.server_configured?'Configured':'Not configured'}</span></div><div class="card"><strong>Provider order</strong>${esc(r.providers.join(' → '))}</div><div class="card"><strong>Pending imported albums</strong><span class="metric">${r.pending_jobs}</span></div><div class="card"><strong>Last activity</strong>${when(r.last_activity)}</div>`;document.querySelector('#ratingsTasks').innerHTML=Object.entries(workflows('ratings')).map(([name,ts])=>`<tr><td>${esc(name)}<div class="muted">${esc(ts[0].description)}</div></td><td>${taskButtons(ts)}</td></tr>`).join('')||'<tr><td class="empty">No rating workflows available.</td></tr>'}catch(e){note(e.message)}}
async function renderMusicBrainz(){try{const m=await api('/api/musicbrainz');document.querySelector('#mbStatus').innerHTML=`<div class="card"><strong>Helper</strong><span class="${m.enabled?'completed':'failed'}">${m.enabled?'Enabled':'Disabled'}</span></div><div class="card"><strong>Account</strong>${esc(m.username||'Not configured')}</div><div class="card"><strong>Authentication</strong><span class="${m.authenticated?'completed':'failed'}">${esc(m.authentication)}</span></div><div class="card"><strong>Queue files</strong>${esc(m.queued_files)}</div>`;document.querySelector('#mbTasks').innerHTML=Object.entries(workflows('musicbrainz')).map(([name,ts])=>`<tr><td>${esc(name)}<div class="muted">${esc(ts[0].description)}</div></td><td>${taskButtons(ts)}</td></tr>`).join('')}catch(e){note(e.message)}}
async function renderConfig(){try{const selected=document.querySelector('#configFile').value,form=document.querySelector('#config'),editor=document.querySelector('#configEditor');configChanges={};document.querySelector('#configState').textContent='';if(selected==='extended'){const d=await api('/api/config');editor.hidden=true;form.hidden=false;form.innerHTML=d.groups.map(g=>`<div class="config-group"><h2>${esc(g.name)}</h2>${g.fields.map(f=>`<div class="field"><label>${esc(f.name)}<small>${esc(f.description)}</small></label>${f.secret?`<div class="secret-row"><input type="password" data-key="${esc(f.name)}" placeholder="${f.configured?'Configured — enter to replace':'Not configured'}"><button onclick="clearSecret('${esc(f.name)}')">Clear</button></div>`:`<input data-key="${esc(f.name)}" value="${esc(f.value)}">`}</div>`).join('')}</div>`).join('');document.querySelectorAll('[data-key]').forEach(e=>e.addEventListener('input',()=>{configChanges[e.dataset.key]=e.value;document.querySelector('#configState').textContent='Unsaved changes'}))}else{const d=await api('/api/config-file?name='+encodeURIComponent(selected));form.hidden=true;editor.hidden=false;editor.value=d.content;configFileContent=d.content;editor.oninput=()=>document.querySelector('#configState').textContent=editor.value===configFileContent?'':'Unsaved changes'}}catch(e){note(e.message)}}
function clearSecret(key){configChanges[key]='';const e=document.querySelector(`[data-key="${CSS.escape(key)}"]`);if(e)e.value='';document.querySelector('#configState').textContent='Secret will be cleared'}
async function saveConfig(){const selected=document.querySelector('#configFile').value;try{let d;if(selected==='extended'){if(!Object.keys(configChanges).length)return note('No configuration changes');d=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({changes:configChanges})})}else{const content=document.querySelector('#configEditor').value;if(content===configFileContent)return note('No configuration changes');d=await api('/api/config-file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:selected,content})})}note('Configuration saved; backup: '+d.backup);await renderConfig();await refresh()}catch(e){note(e.message)}}
async function start(id){try{await api('/api/jobs/'+encodeURIComponent(id),{method:'POST'});await refresh()}catch(e){note(e.message)}}
async function stop(id){try{await api('/api/jobs/'+encodeURIComponent(id)+'/stop',{method:'POST'});await refresh()}catch(e){note(e.message)}}
async function refresh(){try{state=await api('/api/status');document.querySelector('#app').textContent=state.app.toUpperCase();document.querySelector('#updated').textContent='Updated '+new Date().toLocaleTimeString();renderTasks();const s=document.querySelector('#logSelect'),old=s.value;s.innerHTML=Object.entries(state.logs).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');if([...s.options].some(o=>o.value===old))s.value=old;if(document.querySelector('#overview').classList.contains('active'))renderOverview();if(document.querySelector('#ratings').classList.contains('active'))renderRatings();if(document.querySelector('#musicbrainz').classList.contains('active'))renderMusicBrainz()}catch(e){note(e.message)}}
async function loadLog(){try{activeJobLog='';const id=document.querySelector('#logSelect').value;if(!id)return;rawLog=(await api('/api/log?name='+encodeURIComponent(id)+'&lines=500')).content||'Log is empty or has not been created.';renderLog()}catch(e){note(e.message)}}
function renderLog(){const q=document.querySelector('#logFilter').value.toLowerCase();const text=q?rawLog.split('\n').filter(line=>line.toLowerCase().includes(q)).join('\n'):rawLog;const p=document.querySelector('#log');p.textContent=text;p.scrollTop=p.scrollHeight}
async function showJob(id){showPage('logs');try{activeJobLog=id;rawLog=(await api('/api/job-log?id='+encodeURIComponent(id)+'&lines=500')).content||'Log is empty.';renderLog()}catch(e){note(e.message)}}
function showPage(page){if(!document.getElementById(page))page='overview';document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id===page));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===page));location.hash=page;document.querySelector('aside').classList.remove('open');if(page==='overview')renderOverview();if(page==='ratings')renderRatings();if(page==='configuration')renderConfig();if(page==='musicbrainz')renderMusicBrainz();if(page==='logs'&&!rawLog)loadLog()}
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

    def _send(self, value, status=HTTPStatus.OK, content_type="application/json"):
        body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
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
    print(f"arr-scripts dashboard: http://{bind}:{port} ({args.app})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
