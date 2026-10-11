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
export SOULSEEK_FALLBACK_DELAY="${soulseekFallbackDelay:-3600}"
export SOULSEEK_IMPORT_PATH="${importPath:-/downloads/lidarr/import}"
export SOULSEEK_CONFIG_DIR="${ARR_CONFIG_DIR:-/config}"
export SOULSEEK_SOURCES="${dlClientSource:-}"
export SOULSEEK_AUDIO_FORMAT="${audioFormat:-native}"
export SOULSEEK_AUDIO_BITRATE="${audioBitrate:-master}"
export SOULSEEK_REQUIRE_QUALITY="${requireQuality:-false}"
export SOULSEEK_AUDIO_ENABLED="${enableAudio:-false}"
export SOULSEEK_ARR_URL="${arrUrl:-}"
export SOULSEEK_ARR_API_KEY="${arrApiKey:-}"
exec python3 "${ARR_SOULSEEK_WORKER:-/config/extended/python/Soulseek.py}" "$@"
