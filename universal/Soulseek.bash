#!/usr/bin/env bash
# Shared wrapper: configuration stays in extended.conf; secrets stay out of argv.
set -euo pipefail
source "${ARR_EXTENDED_CONF:-/config/extended.conf}"
[[ "${enableSoulseek:-false}" == "true" ]] || exit 0
app="${ARR_SOULSEEK_APP:?Set ARR_SOULSEEK_APP to lidarr, sonarr or radarr}"
if [[ "$app" == lidarr ]]; then
    case ",${dlClientSource:-deezer}," in
        *,soulseek,*|*,all,*) ;;
        *) exit 0 ;;
    esac
fi
export SOULSEEK_APP="$app"
export SOULSEEK_CLIENT="${soulseekClient:-slskd}"
export SOULSEEK_URL="${soulseekUrl:-http://slskd:5030}"
export SOULSEEK_API_KEY="${soulseekApiKey:-}"
export SOULSEEK_DOWNLOAD_PATH="${soulseekDownloadPath:-/downloads/soulseek}"
# Soulseek catalog misses use their own fixed retry. failedDownloadRetryDays may
# be a comma-separated Tidal/Deezer schedule and therefore is not valid here.
export SOULSEEK_FAILED_SEARCH_DAYS="${soulseekFailedSearchDays:-7}"
export SOULSEEK_FAILED_RETRY_DAYS="${soulseekFailedRetryDays:-30}"
export SOULSEEK_OFFLINE_DELAY="${soulseekOfflineDelay:-900}"
export SOULSEEK_OFFLINE_GRACE="${soulseekOfflineGrace:-86400}"
export SOULSEEK_MAX_BATCHES="${soulseekMaxBatches:-3}"
export SOULSEEK_MAX_FOREGROUND_BATCHES="${soulseekMaxForegroundBatches:-${soulseekMaxBatches:-3}}"
export SOULSEEK_NOTFOUND_RETRY_DAYS="${retryNotFound:-90}"
export SOULSEEK_FALLBACK_DELAY="${soulseekFallbackDelay:-3600}"
export SOULSEEK_IMPORT_PATH="${importPath:-/downloads/lidarr/import}"
export SOULSEEK_CONFIG_DIR="${ARR_CONFIG_DIR:-/config}"
export SOULSEEK_SOURCES="${dlClientSource:-}"
export SOULSEEK_AUDIO_FORMAT="${audioFormat:-native}"
export SOULSEEK_AUDIO_BITRATE="${audioBitrate:-master}"
export SOULSEEK_REQUIRE_QUALITY="${requireQuality:-false}"
export SOULSEEK_LOG_LEVEL="${soulseekLogLevel:-info}"
export SOULSEEK_RELEASE_GROUPS="${soulseekReleaseGroups:-}"
export SOULSEEK_RELEASE_GROUP_MODE="${soulseekReleaseGroupMode:-extend}"
export SOULSEEK_AUDIO_ENABLED="${enableAudio:-false}"
export SOULSEEK_ARR_URL="${arrUrl:-}"
export SOULSEEK_ARR_API_KEY="${arrApiKey:-}"

wait_for_arr_ready() {
    local api_key="$SOULSEEK_ARR_API_KEY"
    local api_url="$SOULSEEK_ARR_URL"
    local api_version="v3"
    local config_file="${SOULSEEK_CONFIG_DIR}/config.xml"
    local values=()

    if [[ -z "$api_url" && -f "$config_file" ]]; then
        mapfile -t values < <(python3 - "$config_file" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
base = root.findtext("UrlBase", default="").strip("/")
port = root.findtext("Port", default="")
print(f"http://127.0.0.1:{port}" + (f"/{base}" if base else ""))
print(root.findtext("ApiKey", default=""))
PY
)
        api_url="${values[0]:-}"
        api_key="${values[1]:-}"
    fi
    [[ -n "$api_url" && -n "$api_key" ]] || return 0
    [[ "$app" == "lidarr" ]] && api_version="v1"

    until curl -fsS --connect-timeout 2 --max-time 4 \
        -H "X-Api-Key: $api_key" "${api_url%/}/api/$api_version/system/status" >/dev/null 2>&1; do
        sleep 2
    done
    # The status endpoint begins responding just before the Arr background
    # task manager finishes starting. Avoid racing configuration and imports.
    sleep "${ARR_STARTUP_GRACE_SECONDS:-3}"
}

worker="${ARR_SOULSEEK_WORKER:-/config/extended/python/Soulseek.py}"
if [[ " ${*:-} " == *" --diagnose "* ]]; then
    exec python3 "$worker" "$@"
fi
wait_for_arr_ready
logDir="${SOULSEEK_CONFIG_DIR}/logs"
mkdir -p "$logDir"
logFile="$logDir/Soulseek.log"
logMaxBytes=$((5 * 1024 * 1024))
if [[ -f "$logFile" ]] && (( $(stat -c %s "$logFile") >= logMaxBytes )); then
    rm -f "$logFile.2"
    [[ ! -f "$logFile.1" ]] || mv "$logFile.1" "$logFile.2"
    mv "$logFile" "$logFile.1"
fi
set +e
python3 "$worker" "$@" 2>&1 | tee -a "$logFile"
status=${PIPESTATUS[0]}
set -e
exit "$status"
