#!/usr/bin/env python3
"""Small, dependency-free web dashboard for approved arr-scripts commands."""

import argparse
import base64
import hmac
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


CONFIG_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|'([^']*)'|([^#\s]*))")
CONFIG = Path(os.environ.get("ARR_EXTENDED_CONF", "/config/extended.conf"))
STATE_DIR = Path(os.environ.get("ARR_DASHBOARD_STATE", "/config/extended/dashboard"))
LOG_DIR = Path(os.environ.get("ARR_LOG_DIR", "/config/logs"))
DEFAULT_PORTS = {"lidarr": 8687, "sonarr": 8990, "radarr": 7879, "readarr": 8788}


def read_config(path=CONFIG):
    values = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        match = CONFIG_RE.match(line.strip())
        if match:
            values[match.group(1)] = next(value for value in match.groups()[1:] if value is not None)
    return values


def truthy(value):
    return str(value).casefold() in {"1", "true", "yes", "on"}


def lidarr_tasks(config):
    root = config.get("libraryMaintenanceRoot") or config.get("lyricsLibraryRoot") or "/music"
    tasks = {
        "replaygain-audit": {
            "name": "Audit ReplayGain tags", "kind": "preview", "enabled": truthy(config.get("enableReplaygainTags")),
            "command": ["bash", "/config/extended/ReplayGainTagger.bash", "--audit", "--path", root],
            "description": "Count complete, partial, and missing ReplayGain tags without changing files.",
        },
        "replaygain-preview": {
            "name": "Preview ReplayGain backfill", "kind": "preview", "enabled": truthy(config.get("enableReplaygainTags")),
            "command": ["bash", "/config/extended/ReplayGainTagger.bash", "--backfill", "--dry-run", "--path", root],
            "description": "Show which albums need ReplayGain tags.",
        },
        "replaygain-backfill": {
            "name": "Run ReplayGain backfill", "kind": "write", "enabled": truthy(config.get("enableReplaygainTags")),
            "preview": "replaygain-preview", "command": ["bash", "/config/extended/ReplayGainTagger.bash", "--backfill", "--path", root],
            "description": "Write ReplayGain metadata to albums found by the preview.",
        },
        "lyrics-preview": {
            "name": "Audit missing lyrics", "kind": "preview", "enabled": truthy(config.get("enableLyrics")),
            "command": ["python3", "/config/extended/python/Lyrics.py", "--audit", config.get("lyricsLibraryRoot", "/music")],
            "description": "Count audio files missing embedded lyrics without queuing or writing.",
        },
        "lyrics-backfill": {
            "name": "Queue missing lyrics", "kind": "write", "enabled": truthy(config.get("enableLyrics")),
            "preview": "lyrics-preview",
            "command": ["python3", "/config/extended/python/Lyrics.py", "--enqueue", config.get("lyricsLibraryRoot", "/music"), "--strict-enqueue"],
            "description": "Scan the library and queue files missing lyrics for the background worker.",
        },
        "ratings-preview": {
            "name": "Preview ratings backfill", "kind": "preview", "enabled": truthy(config.get("enableRatings")),
            "command": ["bash", "/config/extended/Ratings.bash", "--library", "--preview"],
            "description": "Look up library ratings without submitting changes.",
        },
        "ratings-backfill": {
            "name": "Run ratings backfill", "kind": "write", "enabled": truthy(config.get("enableRatings")),
            "preview": "ratings-preview", "command": ["bash", "/config/extended/Ratings.bash", "--library"],
            "description": "Fill unrated library items using the configured provider.",
        },
        "release-groups-preview": {
            "name": "Preview release-group repair", "kind": "preview", "enabled": True,
            "command": ["python3", "/config/extended/python/ReleaseGroups.py"],
            "description": "Find blank Lidarr release groups recoverable from trusted filename suffixes.",
        },
        "release-groups-apply": {
            "name": "Apply release-group repair", "kind": "write", "enabled": True,
            "preview": "release-groups-preview", "command": ["python3", "/config/extended/python/ReleaseGroups.py", "--apply"],
            "description": "Write the release groups shown by the most recent preview.",
        },
        "library-audit": {
            "name": "Audit library mappings", "kind": "preview", "enabled": truthy(config.get("enableLibraryMaintenance")),
            "command": ["bash", "/config/extended/LibraryMaintenance.bash", "--audit"],
            "description": "Generate the review-first unmapped-file and optional date audit.",
        },
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
            item["preview"] = task.get("preview")
            item["preview_ready"] = not task.get("preview") or self._preview_time(task["preview"]) >= cutoff
            result.append(item)
        return result

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
:root{color-scheme:dark;--bg:#101418;--panel:#192027;--line:#303b45;--text:#eef3f6;--muted:#9fb0bc;--green:#35c985;--amber:#f1b84b;--red:#ed6a6a}
*{box-sizing:border-box}body{margin:0;font:15px system-ui,sans-serif;background:var(--bg);color:var(--text)}header,main{max-width:1180px;margin:auto;padding:22px}header{display:flex;align-items:end;justify-content:space-between;border-bottom:1px solid var(--line)}h1{margin:0;font-size:27px}h2{font-size:18px;margin:28px 0 12px}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(285px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:15px}.card h3{margin:0 0 7px;font-size:16px}.card p{min-height:42px;color:var(--muted);line-height:1.4}.row{display:flex;gap:8px;align-items:center;justify-content:space-between}button,select{background:#27323b;color:var(--text);border:1px solid #465561;border-radius:6px;padding:8px 11px}button{cursor:pointer}button.primary{background:#167c55;border-color:#23986b}button.write{background:#8a5d13;border-color:#b27a20}button:disabled{opacity:.42;cursor:not-allowed}.pill{font-size:12px;border-radius:99px;padding:3px 8px;background:#29353e}.completed{color:var(--green)}.failed,.interrupted{color:var(--red)}.running,.stopping{color:var(--amber)}table{width:100%;border-collapse:collapse;background:var(--panel);border-radius:9px;overflow:hidden}td,th{text-align:left;padding:10px;border-bottom:1px solid var(--line)}pre{height:360px;overflow:auto;white-space:pre-wrap;background:#090c0e;border:1px solid var(--line);padding:12px;border-radius:7px;color:#dce6eb}.notice{padding:10px 12px;background:#342911;border:1px solid #66501e;border-radius:7px;display:none}@media(max-width:650px){header{align-items:start;flex-direction:column;gap:8px}td:nth-child(3),th:nth-child(3){display:none}}
</style></head><body><header><div><h1>arr-scripts</h1><div class="muted" id="app"></div></div><div id="updated" class="muted"></div></header><main><div id="notice" class="notice"></div><h2>Tasks</h2><div class="grid" id="tasks"></div><h2>Recent jobs</h2><table><thead><tr><th>Task</th><th>Status</th><th>Started</th><th>Action</th></tr></thead><tbody id="jobs"></tbody></table><h2>Logs</h2><div class="row"><select id="logSelect"></select><button onclick="loadLog()">Refresh</button></div><pre id="log">Select a log.</pre></main><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when=v=>v?new Date(v*1000).toLocaleString():'-';
let activeJobLog='';
function note(message){const n=document.querySelector('#notice');n.textContent=message;n.style.display='block';setTimeout(()=>n.style.display='none',7000)}
async function api(path,options){const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw Error(data.error||r.statusText);return data}
async function start(id){try{await api('/api/jobs/'+encodeURIComponent(id),{method:'POST'});await refresh()}catch(e){note(e.message)}}
async function stop(id){try{await api('/api/jobs/'+encodeURIComponent(id)+'/stop',{method:'POST'});await refresh()}catch(e){note(e.message)}}
async function refresh(){try{const d=await api('/api/status');document.querySelector('#app').textContent=d.app.toUpperCase()+' dashboard';document.querySelector('#updated').textContent='Updated '+new Date().toLocaleTimeString();document.querySelector('#tasks').innerHTML=d.tasks.map(t=>`<div class="card"><div class="row"><h3>${esc(t.name)}</h3><span class="pill">${esc(t.kind)}</span></div><p>${esc(t.description)}</p><button class="${t.kind==='write'?'write':'primary'}" ${!t.enabled||!t.preview_ready?'disabled':''} onclick="start('${esc(t.id)}')">${!t.enabled?'Disabled':!t.preview_ready?'Preview required':'Run'}</button></div>`).join('')||'<div class="muted">No task controls are available for this application yet. Logs are still available below.</div>';document.querySelector('#jobs').innerHTML=d.jobs.map(j=>`<tr><td>${esc(j.name)}</td><td class="${esc(j.status)}">${esc(j.status)}</td><td>${when(j.started)}</td><td><button onclick="showJob('${esc(j.id)}')">Log</button>${j.status==='running'?` <button onclick="stop('${esc(j.id)}')">Stop</button>`:''}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">No dashboard jobs yet.</td></tr>';const s=document.querySelector('#logSelect'),old=s.value;s.innerHTML=Object.entries(d.logs).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');if([...s.options].some(o=>o.value===old))s.value=old}catch(e){note(e.message)}}
async function loadLog(){try{activeJobLog='';const id=document.querySelector('#logSelect').value;if(!id)return;const d=await api('/api/log?name='+encodeURIComponent(id)+'&lines=300');const p=document.querySelector('#log');p.textContent=d.content||'Log is empty or has not been created.';p.scrollTop=p.scrollHeight}catch(e){note(e.message)}}
async function showJob(id){try{activeJobLog=id;const d=await api('/api/job-log?id='+encodeURIComponent(id)+'&lines=300');const p=document.querySelector('#log');p.textContent=d.content||'Log is empty.';p.scrollTop=p.scrollHeight}catch(e){note(e.message)}}
refresh();setInterval(()=>{refresh();if(activeJobLog)showJob(activeJobLog);else if(document.querySelector('#logSelect').value)loadLog()},5000);
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
        match = re.fullmatch(r"/api/jobs/([a-z0-9-]+)(/stop)?", urlparse(self.path).path)
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
