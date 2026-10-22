#!/usr/bin/env bash
# Thin adapter: reusable audit/queue logic belongs to navidrome-scripts.
conf="${ARR_EXTENDED_CONF:-/config/extended.conf}"
[ ! -f "$conf" ] || source "$conf"
[ "${enableLibraryMaintenance:-false}" = true ] || exit 0
set -euo pipefail
pin="${navidromeScriptsVersion:-}"
if ! [[ "$pin" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo 'Library maintenance: set navidromeScriptsVersion to a published package version first'
    exit 1
fi
python3 - "$pin" <<'PY'
from importlib.metadata import version
import sys
if version('navidrome-scripts') != sys.argv[1]:
    raise SystemExit('Library maintenance: installed package does not match the configured version pin')
PY
helper="${ARR_LIBRARY_MAINTENANCE_HELPER:-navidrome-maintenance}"
configDir="${ARR_CONFIG_DIR:-/config}"
if [ -z "${LIDARR_URL:-}" ] || [ -z "${LIDARR_API_KEY:-}" ]; then
    mapfile -t credentials < <(python3 - "$configDir/config.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
c = ET.parse(sys.argv[1]).getroot()
print('http://127.0.0.1:' + (c.findtext('Port') or '8686') + '/' + (c.findtext('UrlBase') or '').strip('/'))
print(c.findtext('ApiKey') or '')
PY
)
    [ "${#credentials[@]}" -eq 2 ] || exit 1
    # Native Windows Python emits CRLF even from LF source files. Do not pass a
    # trailing carriage return into an HTTP URL/header when testing in Git Bash.
    credentials[0]="${credentials[0]%$'\r'}"
    credentials[1]="${credentials[1]%$'\r'}"
    [ -n "${credentials[1]}" ] || exit 1
    export LIDARR_URL="${LIDARR_URL:-${credentials[0]%/}}"
    export LIDARR_API_KEY="${LIDARR_API_KEY:-${credentials[1]}}"
fi
export EXTENDED_CONF="$conf"
export LYRICS_URL="${lyricsUrl:-https://api.lrcmux.dev}"
root="${libraryMaintenanceRoot:-/music}"
remoteRoot="${libraryMaintenanceRemoteRoot:-$root}"
state="${libraryMaintenanceState:-$configDir/extended/library-maintenance}"
steps=()
[ "${libraryMaintenanceBeetsPreview:-true}" != true ] || steps+=(beets-preview)
if [ "${enableReplaygainTags:-false}" = true ]; then
    [ "${replaygainPreserveMtime:-true}" = true ] || { echo 'Library maintenance requires replaygainPreserveMtime=true'; exit 1; }
    steps+=(replaygain)
fi
[ "${enableLyrics:-${enableBeetsLyrics:-true}}" != true ] || steps+=(lyrics)
joined="$(IFS=,; echo "${steps[*]}")"
profile="$(python3 - "$joined" "${replaygainTargetLoudness:--18}" "${replaygainClipMode:-p}" "${replaygainTruePeak:-false}" "${replaygainMaxPeak:-0}" "$LYRICS_URL" <<'PY'
import hashlib, json, sys
print(hashlib.sha256(json.dumps(sys.argv[1:]).encode()).hexdigest())
PY
)"
profile="${profile%$'\r'}"
base=(--library-root "$root" --state "$state" --profile "$profile")
rename_command() {
    export NAVIDROME_URL="${NAVIDROME_URL:-${navidromeUrl:-${subsonicUrl:-}}}"
    export NAVIDROME_USERNAME="${NAVIDROME_USERNAME:-${navidromeUsername:-${subsonicUsername:-}}}"
    export NAVIDROME_PASSWORD="${NAVIDROME_PASSWORD:-${navidromePassword:-${subsonicPassword:-}}}"
    "${ARR_LIBRARY_RENAMES_HELPER:-navidrome-renames}" --library-root "$root" "$@"
}
case "${1:---work}" in
    --refresh-plan)
        "${ARR_LIBRARY_UNMAPPED_HELPER:-navidrome-unmapped}" --library-root "$root" refresh-plan --remote-root "$remoteRoot" --album-id "${2:?Lidarr album ID required}" --output "${3:?Output refresh plan required}"
        ;;
    --refresh)
        "${ARR_LIBRARY_UNMAPPED_HELPER:-navidrome-unmapped}" --library-root "$root" refresh --plan "${2:?Reviewed refresh plan required}" --journal "${3:?Refresh journal required}"
        ;;
    --tags-plan)
        "${ARR_LIBRARY_TAGS_HELPER:-navidrome-tags}" --library-root "$root" plan --remote-root "$remoteRoot" --album-id "${2:?Lidarr album ID required}" --output "${3:?Output metadata plan required}"
        ;;
    --tags-apply)
        "${ARR_LIBRARY_TAGS_HELPER:-navidrome-tags}" --library-root "$root" apply --plan "${2:?Reviewed metadata plan required}" --journal "${3:?Metadata journal required}"
        ;;
    --tags-rollback)
        "${ARR_LIBRARY_TAGS_HELPER:-navidrome-tags}" --library-root "$root" rollback --journal "${2:?Metadata journal required}"
        ;;
    --rename-plan)
        rename_command plan --remote-root "$remoteRoot" --album-id "${2:?Lidarr album ID required}" --navidrome-prefix "${libraryMaintenanceNavidromePrefix:-}" --output "${3:?Output rename plan required}"
        ;;
    --rename-apply)
        rename_command apply --plan "${2:?Reviewed rename plan required}" --journal "${3:?Rename journal required}"
        ;;
    --rename-verify)
        rename_command verify --plan "${2:?Rename plan required}" --journal "${3:?Rename journal required}"
        ;;
    --rename-rollback-plan)
        rename_command rollback-plan --plan "${2:?Rename plan required}" --journal "${3:?Rename journal required}" --output "${4:?Output rollback plan required}"
        ;;
    --rename-rollback)
        rename_command rollback --plan "${2:?Reviewed rollback plan required}" --journal "${3:?Rollback journal required}"
        ;;
    --rename-rollback-verify)
        rename_command rollback-verify --plan "${2:?Rollback plan required}" --journal "${3:?Rollback journal required}"
        ;;
    --date-plan)
        extra=()
        if [ "${4:-}" = --apple-review ]; then
            extra+=(--apple-review)
        elif [ -n "${4:-}" ]; then
            echo 'Optional date-plan argument: --apple-review'; exit 2
        fi
        "${ARR_LIBRARY_DATES_HELPER:-navidrome-dates}" --library-root "$root" plan --lookup-dates --audit "${2:?Date audit file required}" --output "${3:?Output date plan required}" "${extra[@]}"
        ;;
    --date-apply)
        "${ARR_LIBRARY_DATES_HELPER:-navidrome-dates}" --library-root "$root" apply --check-lidarr --plan "${2:?Reviewed date plan required}" --journal "${3:?Date journal required}"
        ;;
    --date-rollback)
        "${ARR_LIBRARY_DATES_HELPER:-navidrome-dates}" --library-root "$root" rollback --journal "${2:?Date journal required}"
        ;;
    --plan)
        "${ARR_LIBRARY_UNMAPPED_HELPER:-navidrome-unmapped}" --library-root "$root" plan --remote-root "$remoteRoot" --path "${2:?Remote album path required}" --output "${3:?Output plan file required}"
        ;;
    --import)
        "${ARR_LIBRARY_UNMAPPED_HELPER:-navidrome-unmapped}" --library-root "$root" apply --plan "${2:?Reviewed plan file required}" --journal "${3:?Import journal file required}"
        ;;
    --catalog)
        "${ARR_LIBRARY_UNMAPPED_HELPER:-navidrome-unmapped}" --library-root "$root" add-catalog --plan "${2:?Reviewed plan file required}" --journal "${3:?Catalog journal file required}"
        ;;
    --approve)
        "$helper" "${base[@]}" approve --handoff "${2:?Verified handoff file required}" --steps "$joined"
        ;;
    --work)
        # The service runs as the same account as Lidarr; callbacks never wait for it.
        "$helper" "${base[@]}" work --hook-dir "${ARR_LIBRARY_HOOK_DIR:-$configDir/extended}" --config-dir "$configDir" --steps "$joined"
        ;;
    --audit)
        extra=()
        [ "${libraryMaintenanceDateAudit:-false}" != true ] || extra+=(--include-dates)
        "$helper" "${base[@]}" audit --remote-root "$remoteRoot" --days "${libraryMaintenanceDays:-7}" "${extra[@]}"
        ;;
    *) echo 'Usage: LibraryMaintenance.bash --plan PATH OUTPUT | --import PLAN JOURNAL | --catalog PLAN JOURNAL | --refresh-plan ALBUM_ID OUTPUT | --refresh PLAN JOURNAL | --tags-plan ALBUM_ID OUTPUT | --tags-apply PLAN JOURNAL | --tags-rollback JOURNAL | --date-plan AUDIT OUTPUT | --date-apply PLAN JOURNAL | --date-rollback JOURNAL | --rename-plan ALBUM_ID OUTPUT | --rename-apply PLAN JOURNAL | --rename-verify PLAN JOURNAL | --rename-rollback-plan PLAN JOURNAL OUTPUT | --rename-rollback PLAN JOURNAL | --rename-rollback-verify PLAN JOURNAL | --approve HANDOFF | --work | --audit'; exit 2 ;;
esac
