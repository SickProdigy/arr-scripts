#!/usr/bin/env bash
scriptVersion="1.2"
scriptName="ReplayGainTagger"

set -o pipefail

configFile="${EXTENDED_CONF:-/config/extended.conf}"
functionsFile="${FUNCTIONS_PATH:-/config/extended/functions}"
if [ -f "$configFile" ]; then
	# shellcheck source=/dev/null
	source "$configFile"
fi
if [ -f "$functionsFile" ]; then
	# shellcheck source=/dev/null
	source "$functionsFile"
fi

if ! declare -F log >/dev/null 2>&1; then
	log () {
		printf '%s :: %s :: %s :: %s\n' "$(date "+%F %T")" "$scriptName" "$scriptVersion" "$1"
	}
fi

activeLockDir=""
cleanup_active_lock () {
	if [ -n "$activeLockDir" ]; then
		rmdir "$activeLockDir" 2>/dev/null || true
		activeLockDir=""
	fi
}
trap cleanup_active_lock EXIT
trap 'cleanup_active_lock; exit 130' INT TERM

replaygainTargetLoudness="${replaygainTargetLoudness:--18}"
replaygainThreads="${replaygainThreads:-1}"
replaygainPreserveMtime="${replaygainPreserveMtime:-true}"
replaygainClipMode="${replaygainClipMode:-p}"
replaygainTruePeak="${replaygainTruePeak:-false}"
replaygainMaxPeak="${replaygainMaxPeak:-0}"
replaygainScanner="${REPLAYGAIN_SCANNER:-rsgain}"
replaygainStateDir="${REPLAYGAIN_STATE_DIR:-/config/extended/replaygain}"
replaygainMusicRoot="${replaygainMusicRoot:-${REPLAYGAIN_MUSIC_ROOT:-/music}}"
arrUrl="${arrUrl:-}"
arrApiKey="${arrApiKey:-}"
mode="event"
dryRun="false"
forceScan="false"
explicitPath=""
repairAudit=""

usage () {
	cat <<'EOF'
Usage:
  ReplayGainTagger.bash
  ReplayGainTagger.bash --path /music/Artist/Album
  ReplayGainTagger.bash --maintenance
  ReplayGainTagger.bash --audit [--path /music]
  ReplayGainTagger.bash --backfill [--dry-run] [--path /music]
  ReplayGainTagger.bash --repair-audit /config/logs/replaygain-audit.log [--dry-run] [--force] [--path /music]

Default mode is for Lidarr custom-script release import and upgrade events.
Backfill and normal event mode write ReplayGain metadata tags; they do not modify audio samples.
Audit mode is read-only.
EOF
}

while [ "$#" -gt 0 ]; do
	case "$1" in
		--maintenance)
			mode="maintenance"
			;;
		--audit)
			mode="audit"
			;;
		--force)
			forceScan="true"
			;;
		--backfill)
			mode="backfill"
			;;
		--repair-audit)
			mode="repair"
			shift
			repairAudit="${1:-}"
			;;
		--dry-run)
			dryRun="true"
			;;
		--path)
			shift
			explicitPath="${1:-}"
			;;
		--help|-h)
			usage
			exit 0
			;;
		*)
			if [ -z "$explicitPath" ]; then
				explicitPath="$1"
			else
				log "ERROR :: Unknown argument: $1"
				usage
				exit 2
			fi
			;;
	esac
	shift
done

if [ "${lidarr_eventtype:-}" = "Test" ]; then
	log "Tested Successfully"
	exit 0
fi

if [ "${enableReplaygainTags:-false}" != "true" ] && [ "$mode" != "audit" ]; then
	log "ReplayGain tagging is disabled; set enableReplaygainTags=true in /config/extended.conf to enable it"
	exit 0
fi

if ! [[ "$replaygainTargetLoudness" =~ ^-([5-9]|[12][0-9]|30)$ ]]; then
	log "ERROR :: replaygainTargetLoudness must be an integer from -30 through -5 LUFS; refusing nonstandard/invalid value: $replaygainTargetLoudness"
	exit 2
fi

if [ "$replaygainThreads" != "MAX" ] && ! [[ "$replaygainThreads" =~ ^[1-9][0-9]*$ ]]; then
	log "ERROR :: replaygainThreads must be a positive integer or MAX"
	exit 2
fi

if ! [[ "$replaygainClipMode" =~ ^(n|p|a)$ ]]; then
	log "ERROR :: replaygainClipMode must be n (none), p (positive gain), or a (always)"
	exit 2
fi

if ! [[ "$replaygainTruePeak" =~ ^(true|false)$ ]]; then
	log "ERROR :: replaygainTruePeak must be true or false"
	exit 2
fi

if ! [[ "$replaygainMaxPeak" =~ ^(-[0-9]+([.][0-9]+)?|0([.]0+)?)$ ]]; then
	log "ERROR :: replaygainMaxPeak must be zero or a negative dB value"
	exit 2
fi

python_realpath () {
	python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

path_inside_root () {
	local pathReal rootReal
	pathReal="$(python_realpath "$1")" || return 1
	rootReal="$(python_realpath "$2")" || return 1
	python3 -c 'import os,sys; sys.exit(0 if os.path.commonpath([sys.argv[1], sys.argv[2]]) == sys.argv[2] else 1)' "$pathReal" "$rootReal"
}

hash_path () {
	printf '%s' "$1" | sha256sum | awk '{print $1}'
}

album_fingerprint () {
	python3 - "$1" <<'PY'
import hashlib
import os
import sys

root = os.path.realpath(sys.argv[1])
extensions = {".flac", ".mp3", ".m4a", ".aac", ".opus"}
digest = hashlib.sha256()
for current, _, files in os.walk(root):
    for name in sorted(files):
        if os.path.splitext(name)[1].lower() not in extensions:
            continue
        path = os.path.join(current, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        rel = os.path.relpath(path, root)
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode())
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode())
        digest.update(b"\0")
print(digest.hexdigest())
PY
}

audio_find_expr=(
	-type f
	\( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.aac' -o -iname '*.opus' \)
)

resolve_music_root () {
	if [ -n "${lidarr_artist_path:-}" ]; then
		dirname "$lidarr_artist_path"
	else
		printf '%s\n' "$replaygainMusicRoot"
	fi
}

event_paths_from_env () {
	if [ -n "$explicitPath" ]; then
		printf '%s\n' "$explicitPath"
	fi
	if [ -n "${lidarr_trackfile_path:-}" ]; then
		printf '%s\n' "$lidarr_trackfile_path"
	fi
	if [ -n "${lidarr_artist_path:-}" ] && [ -n "${lidarr_album_title:-}" ]; then
		printf '%s\n' "$lidarr_artist_path/$lidarr_album_title"
	fi
	for varName in lidarr_trackfile_paths lidarr_addedtrackpaths lidarr_importedtrackpaths lidarr_deletedpaths; do
		value="${!varName:-}"
		if [ -n "$value" ]; then
			printf '%s\n' "$value" | tr '|' '\n'
		fi
	done
}

resolve_album_path_from_api () {
	if [ -z "${lidarr_album_id:-}" ] && [ -n "${1:-}" ]; then
		lidarr_album_id="$1"
	fi
	if [ -z "${lidarr_album_id:-}" ]; then
		return 1
	fi
	if ! declare -F getArrAppInfo >/dev/null 2>&1 || ! declare -F verifyApiAccess >/dev/null 2>&1; then
		return 1
	fi
	getArrAppInfo
	verifyApiAccess
	if [ -z "$arrUrl" ] || [ -z "$arrApiKey" ]; then
		return 1
	fi
	local trackPath
	trackPath="$(curl -s "$arrUrl/api/v1/trackFile?albumId=$lidarr_album_id" -H "X-Api-Key: ${arrApiKey}" | jq -r '.[0].path // empty')"
	if [ -n "$trackPath" ]; then
		dirname "$trackPath"
	fi
}

first_existing_album_path () {
	local candidate
	while IFS= read -r candidate; do
		[ -z "$candidate" ] && continue
		if [ -f "$candidate" ]; then
			candidate="$(dirname "$candidate")"
		fi
		if [ -d "$candidate" ]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done
	return 1
}

normalize_album_path () {
	python3 - "$1" "$2" <<'PY'
import os
import sys

path = os.path.realpath(sys.argv[1])
root = os.path.realpath(sys.argv[2])
if os.path.isfile(path):
    path = os.path.dirname(path)
relative = os.path.relpath(path, root)
parts = [] if relative == "." else relative.split(os.sep)
if len(parts) >= 2:
    print(os.path.join(root, parts[0], parts[1]))
else:
    print(path)
PY
}

collect_audio_files () {
	find "$1" "${audio_find_expr[@]}" -print0
}

count_audio_files () {
	find "$1" "${audio_find_expr[@]}" | wc -l | awk '{print $1}'
}

discover_album_dirs () {
	python3 - "$1" "$2" <<'PY'
import os
import sys

target = os.path.realpath(sys.argv[1])
root = os.path.realpath(sys.argv[2])
extensions = {".flac", ".mp3", ".m4a", ".aac", ".opus"}
albums = set()

for current, _, files in os.walk(target):
    for name in files:
        if os.path.splitext(name)[1].lower() not in extensions:
            continue
        relative_dir = os.path.relpath(current, root)
        parts = [] if relative_dir == "." else relative_dir.split(os.sep)
        # Lidarr libraries are rooted as Artist/Album. Collapse any deeper disc
        # directories so all discs contribute to one album-gain calculation.
        if len(parts) >= 2:
            albums.add(os.path.join(root, parts[0], parts[1]))
        else:
            albums.add(current)

for album in sorted(albums):
    sys.stdout.buffer.write(os.fsencode(album) + b"\0")
PY
}

run_rsgain_album () {
	local albumPath="$1"
	local rootPath="$2"
	local status commandStatus
	local pathHash lockDir stampFile
	local currentFingerprint
	pathHash="$(hash_path "$(python_realpath "$albumPath")")"
	lockDir="$replaygainStateDir/locks/$pathHash.lock"
	stampFile="$replaygainStateDir/stamps/$pathHash.done"
	mkdir -p "$replaygainStateDir/locks" "$replaygainStateDir/stamps"

	if ! path_inside_root "$albumPath" "$rootPath"; then
		log "ERROR :: Refusing ReplayGain scan outside music root: $albumPath (root: $rootPath)"
		return 2
	fi
	currentFingerprint="$(printf 'replaygain-v2\0target=%s\0clip=%s\0truepeak=%s\0maxpeak=%s\0files=%s' \
		"$replaygainTargetLoudness" "$replaygainClipMode" "$replaygainTruePeak" "$replaygainMaxPeak" \
		"$(album_fingerprint "$albumPath")" | sha256sum | awk '{print $1}')"
	if [ "$forceScan" != "true" ] && [ "$mode" != "repair" ] && [ -f "$stampFile" ] && [ "$(cat "$stampFile")" = "$currentFingerprint" ]; then
		log "$albumPath :: ReplayGain scan already completed for this path; skipping duplicate event"
		return 0
	fi
	if ! mkdir "$lockDir" 2>/dev/null; then
		log "$albumPath :: ReplayGain scan is already running; skipping duplicate event"
		[ "$mode" != "repair" ] || return 75
		return 0
	fi
	activeLockDir="$lockDir"

	local fileCount
	fileCount="$(count_audio_files "$albumPath")"
	if [ "$fileCount" -eq 0 ]; then
		log "$albumPath :: No supported audio files found for ReplayGain"
		cleanup_active_lock
		return 0
	fi

	local preserveArgs=()
	if [ "$replaygainPreserveMtime" = "true" ]; then
		preserveArgs=(-p)
	fi
	local truePeakArgs=()
	if [ "$replaygainTruePeak" = "true" ]; then
		truePeakArgs=(-t)
	fi

	local files=()
	while IFS= read -r -d '' file; do
		files+=("$file")
	done < <(collect_audio_files "$albumPath")

	log "$albumPath :: Running ReplayGain scan with rsgain; files=$fileCount target=${replaygainTargetLoudness} LUFS clipMode=$replaygainClipMode truePeak=$replaygainTruePeak maxPeak=${replaygainMaxPeak} dB preserveMtime=$replaygainPreserveMtime"
	log "$albumPath :: Command: $replaygainScanner custom -a -s i -l $replaygainTargetLoudness -c $replaygainClipMode -m $replaygainMaxPeak ${truePeakArgs[*]} ${preserveArgs[*]} <${#files[@]} files>"
	"$replaygainScanner" custom -a -s i -l "$replaygainTargetLoudness" -c "$replaygainClipMode" -m "$replaygainMaxPeak" "${truePeakArgs[@]}" "${preserveArgs[@]}" "${files[@]}" 2>&1 | while IFS= read -r line; do
		log "$albumPath :: rsgain :: $line"
	done
	commandStatus=${PIPESTATUS[0]}
	log "$albumPath :: ReplayGain scanner exited with status $commandStatus"
	if [ "$mode" = "repair" ] && [ "$commandStatus" -eq 0 ]; then
		if ! album_tags_complete "$albumPath"; then
			log "$albumPath :: ERROR :: Scanner returned success but ReplayGain tags remain incomplete"
			commandStatus=1
		fi
	fi
	if [ "$commandStatus" -eq 0 ]; then
		printf '%s\n' "$currentFingerprint" > "$stampFile"
		status=0
	else
		log "$albumPath :: WARNING :: ReplayGain tagging failed; Lidarr import will not be rejected"
		status=0
		[ "$mode" != "repair" ] || status="$commandStatus"
	fi
	cleanup_active_lock
	return "$status"
}

album_tags_complete () {
	python3 - "$1" <<'PY'
import os
import sys
from mutagen import File

required = ('REPLAYGAIN_TRACK_GAIN', 'REPLAYGAIN_TRACK_PEAK',
            'REPLAYGAIN_ALBUM_GAIN', 'REPLAYGAIN_ALBUM_PEAK')
bad = 0
for current, _, files in os.walk(sys.argv[1]):
    for name in files:
        if os.path.splitext(name)[1].lower() not in {'.flac', '.mp3', '.m4a', '.aac', '.opus'}:
            continue
        path = os.path.join(current, name)
        try:
            audio = File(path)
            tags = getattr(audio, 'tags', None) or {}
            keys = [str(k).upper().replace('-', '_') for k in tags]
            complete = audio is not None and all(any(tag in key for key in keys) for tag in required)
        except Exception:
            complete = False
        if not complete:
            print('REPAIR_INCOMPLETE\t' + path)
            bad += 1
sys.exit(1 if bad else 0)
PY
}

repair_from_audit () {
	local target="$1" rootPath="$2" albumList albumDir
	local repaired=0 complete=0 failed=0 selected=0
	if [ ! -f "$repairAudit" ]; then
		log "ERROR :: --repair-audit requires an existing audit log"
		return 2
	fi
	if ! path_inside_root "$target" "$rootPath"; then
		log "ERROR :: Refusing repair outside music root: $target"
		return 2
	fi
	albumList="$(mktemp)"
	if ! python3 - "$repairAudit" "$rootPath" "$target" > "$albumList" <<'PY'
import os
import sys

audit, root, target = sys.argv[1:]
root, target = os.path.realpath(root), os.path.realpath(target)
albums = set()
missing = 0
with open(audit, encoding='utf-8-sig') as stream:
    for line in stream:
        state, sep, path = line.rstrip('\r\n').partition('\t')
        if not sep or state not in ('UNTAGGED', 'PARTIAL'):
            continue
        if not os.path.isabs(path) or os.path.commonpath([root, os.path.realpath(path)]) != root:
            raise ValueError('audit contains a file outside the music root')
        if os.path.commonpath([target, os.path.realpath(path)]) != target:
            continue
        if not os.path.isfile(path):
            missing += 1
            continue
        parts = os.path.relpath(path, root).split(os.sep)
        if len(parts) < 3:
            print('REPAIR_REVIEW\t' + path + '\tno album folder; assign an album before calculating album gain', file=sys.stderr)
            continue
        album = os.path.join(root, *parts[:2])
        albums.add(album)
for album in sorted(albums):
    sys.stdout.buffer.write(os.fsencode(album) + b'\0')
if missing:
    print(f'ReplayGain repair: skipped {missing} audit paths no longer present', file=sys.stderr)
PY
	then
		rm -f "$albumList"
		log "ERROR :: Unable to build repair plan; no files scanned"
		return 2
	fi
	log "Targeted repair: only albums containing PARTIAL/UNTAGGED audit entries; full albums are scanned for consistent album gain."
	while IFS= read -r -d '' albumDir; do
		selected=$((selected + 1))
		if [ "$dryRun" = "true" ]; then
			log "REPAIR_PREVIEW :: $albumDir"
		elif [ "$forceScan" != "true" ] && album_tags_complete "$albumDir" >/dev/null; then
			complete=$((complete + 1))
			log "$albumDir :: Already complete; no repair needed"
		elif run_rsgain_album "$albumDir" "$rootPath"; then
			repaired=$((repaired + 1))
		else
			failed=$((failed + 1))
			log "$albumDir :: ERROR :: Repair incomplete or locked; retained for retry"
		fi
	done < "$albumList"
	rm -f "$albumList"
	log "REPAIR SUMMARY :: selected=$selected repaired=$repaired already_complete=$complete failed=$failed dry_run=$dryRun"
	[ "$failed" -eq 0 ]
}

audit_path () {
	local target="$1"
	local rootPath="$2"
	if ! path_inside_root "$target" "$rootPath"; then
		log "ERROR :: Refusing audit outside music root: $target (root: $rootPath)"
		return 2
	fi
	local auditStarted=$SECONDS auditStatus
	log "AUDIT START :: $(date -u +%FT%TZ) :: $target"
	python3 - "$target" "$rootPath" <<'PY'
import os
import sys
from collections import defaultdict

try:
    from mutagen import File
except Exception as exc:
    print(f"AUDIT ERROR: mutagen is required for audit mode: {exc}")
    sys.exit(2)

root = os.path.realpath(sys.argv[1])
music_root = os.path.realpath(sys.argv[2])
extensions = {".flac", ".mp3", ".m4a", ".aac", ".opus"}
known_audio_extensions = extensions | {
    ".aif", ".aiff", ".ape", ".dsf", ".mka", ".mkv", ".mp2", ".oga",
    ".ogg", ".spx", ".tak", ".wav", ".wave", ".webm", ".wma", ".wv",
}
states = ("complete", "partial", "untagged", "unreadable", "unsupported")
summary = {state: 0 for state in states}
albums = defaultdict(lambda: {state: 0 for state in states})

def album_root(current):
    relative = os.path.relpath(os.path.realpath(current), music_root)
    parts = [] if relative == "." else relative.split(os.sep)
    if len(parts) >= 2:
        return os.path.join(music_root, parts[0], parts[1])
    return current

def has_tag(tags, wanted):
    if not tags:
        return False
    wanted = wanted.upper()
    for key in tags.keys():
        if wanted in str(key).upper().replace("-", "_"):
            return True
    return False

def scan_error(error):
    raise error

if not os.path.isdir(root):
    raise FileNotFoundError(root)

for current, _, files in os.walk(root, onerror=scan_error):
    for name in files:
        extension = os.path.splitext(name)[1].lower()
        if extension not in known_audio_extensions:
            continue
        path = os.path.join(current, name)
        album = album_root(current)
        if extension not in extensions:
            state = "unsupported"
        else:
            try:
                audio = File(path)
                tags = getattr(audio, "tags", None)
            except Exception:
                audio = None
                tags = None
            if audio is None:
                state = "unreadable"
            else:
                checks = [
                    has_tag(tags, "REPLAYGAIN_TRACK_GAIN"),
                    has_tag(tags, "REPLAYGAIN_TRACK_PEAK"),
                    has_tag(tags, "REPLAYGAIN_ALBUM_GAIN"),
                    has_tag(tags, "REPLAYGAIN_ALBUM_PEAK"),
                ]
                if all(checks):
                    state = "complete"
                elif any(checks):
                    state = "partial"
                else:
                    state = "untagged"
        summary[state] += 1
        albums[album][state] += 1
        print(f"{state.upper()}\t{path}")

mixed = 0
for album, counts in sorted(albums.items()):
    present_states = [name for name, count in counts.items() if count]
    if len(present_states) > 1:
        mixed += 1
        print(
            "MIXED_ALBUM\t"
            f"complete={counts['complete']}\tpartial={counts['partial']}\t"
            f"untagged={counts['untagged']}\tunreadable={counts['unreadable']}\t"
            f"unsupported={counts['unsupported']}\t{album}"
        )

print(
    "SUMMARY\t"
    f"complete={summary['complete']}\tpartial={summary['partial']}\t"
    f"untagged={summary['untagged']}\tunreadable={summary['unreadable']}\t"
    f"unsupported={summary['unsupported']}\t"
    f"mixed_albums={mixed}"
)
PY
	auditStatus=$?
	log "AUDIT END :: $(date -u +%FT%TZ) :: elapsed_seconds=$((SECONDS - auditStarted)) status=$auditStatus"
	return "$auditStatus"
}

backfill_path () {
	local target="$1"
	local rootPath="$2"
	local albumList albumCount parallelism
	if ! path_inside_root "$target" "$rootPath"; then
		log "ERROR :: Refusing backfill outside music root: $target (root: $rootPath)"
		return 2
	fi
	log "$target :: Backfill requested. This writes ReplayGain metadata tags only; audio samples are not modified."
	log "$target :: Existing untagged or partially tagged files will be scanned at ${replaygainTargetLoudness} LUFS."
	if [ "$dryRun" = "true" ]; then
		log "$target :: Dry run audit only; no metadata will be written."
		audit_path "$target" "$rootPath"
		return $?
	fi
	albumList="$(mktemp)"
	discover_album_dirs "$target" "$rootPath" > "$albumList"
	albumCount="$(tr -cd '\000' < "$albumList" | wc -c | awk '{print $1}')"
	log "$target :: Backfill album directories discovered=$albumCount threads=$replaygainThreads"
	if [ "$albumCount" -eq 0 ]; then
		rm -f "$albumList"
		return 0
	fi
	if [ "$replaygainThreads" = "MAX" ]; then
		parallelism="$(nproc 2>/dev/null || printf '1')"
	else
		parallelism="$replaygainThreads"
	fi
	if [ "$parallelism" -gt 1 ]; then
		xargs -0 -r -P "$parallelism" -I {} "$0" --path "{}" < "$albumList"
	else
		local albumDir processed=0
		while IFS= read -r -d '' albumDir; do
			processed=$((processed + 1))
			log "$target :: Backfill album $processed/$albumCount :: $albumDir"
			run_rsgain_album "$albumDir" "$rootPath"
		done < "$albumList"
	fi
	rm -f "$albumList"
	log "$target :: Backfill complete; album directories processed=$albumCount"
}

maintenance () {
    [ "${enableReplaygainMaintenance:-false}" = "true" ] || return 0
    local interval="${replaygainMaintenanceDays:-7}" now last=0 status
    if ! [[ "$interval" =~ ^[1-9][0-9]*$ ]]; then
        log "ERROR :: replaygainMaintenanceDays must be a positive integer"
        return 2
    fi
    mkdir -p "$replaygainStateDir" || return 1
    command -v flock >/dev/null 2>&1 || { log "ERROR :: flock is required for maintenance"; return 2; }
    exec 9>"$replaygainStateDir/maintenance.lock"
    flock -n 9 || return 0
    now=$(date +%s)
    if [ -f "$replaygainStateDir/maintenance.last-attempt" ]; then
        read -r last < "$replaygainStateDir/maintenance.last-attempt"
    fi
    [[ "$last" =~ ^[0-9]+$ ]] || last=0
    [ "$((now - last))" -ge "$((interval * 86400))" ] || return 0
    # Persist attempts so failures do not cause an hourly full-library retry.
    printf '%s\n' "$now" > "$replaygainStateDir/maintenance.last-attempt"
    repairAudit="$replaygainStateDir/maintenance-audit.log"
    log "MAINTENANCE START :: $(date -u +%FT%TZ)"
    audit_path "$musicRoot" "$musicRoot" > "$repairAudit"
    status=$?
    if [ "$status" -eq 0 ]; then
        repair_from_audit "$musicRoot" "$musicRoot"
        status=$?
    fi
    log "MAINTENANCE END :: $(date -u +%FT%TZ) :: elapsed_seconds=$(($(date +%s) - now)) status=$status audit=$repairAudit"
    return "$status"
}

musicRoot="$(resolve_music_root)"

case "$mode" in
	maintenance)
		maintenance
		exit $?
		;;
	repair)
		targetPath="${explicitPath:-$musicRoot}"
		repair_from_audit "$targetPath" "$musicRoot"
		exit $?
		;;
	audit)
		targetPath="${explicitPath:-$musicRoot}"
		audit_path "$targetPath" "$musicRoot"
		exit $?
		;;
	backfill)
		targetPath="${explicitPath:-$musicRoot}"
		backfill_path "$targetPath" "$musicRoot"
		;;
	event)
		if ! albumPath="$(event_paths_from_env | first_existing_album_path)"; then
			albumPath="$(resolve_album_path_from_api "${1:-}")" || true
		fi
		if [ -z "${albumPath:-}" ] || [ ! -d "$albumPath" ]; then
			log "WARNING :: Unable to resolve imported album path from Lidarr event; skipping ReplayGain"
			exit 0
		fi
		albumPath="$(normalize_album_path "$albumPath" "$musicRoot")"
		run_rsgain_album "$albumPath" "$musicRoot"
		;;
esac

exit 0
