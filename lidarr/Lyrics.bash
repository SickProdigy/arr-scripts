#!/usr/bin/env bash
# Import hooks only enqueue locally. Provider requests belong to the service.
umask 0002
# Run file writes as the same user as Lidarr, including manual docker exec calls.
if [[ "${1:-}" = --work || "${1:-}" = --maintenance ]] && [ "$(id -u)" = 0 ] && command -v s6-setuidgid >/dev/null; then
    exec s6-setuidgid abc bash "$0" "$@"
fi
source "${ARR_EXTENDED_CONF:-/config/extended.conf}"
[ "${enableLyrics:-${enableBeetsLyrics:-true}}" = true ] || exit 0
export LYRICS_CONFIG="${ARR_CONFIG_DIR:-/config}"
export LYRICS_URL="${lyricsUrl:-https://api.lrcmux.dev}"
export LYRICS_ROOTS="${lyricsLibraryRoot:-/music}"
helper="${ARR_LYRICS_WORKER:-/config/extended/python/Lyrics.py}"
if [ "${1:-}" = --maintenance ]; then
    [ "${enableLyricsMaintenance:-false}" = true ] || exit 0
    interval="${lyricsMaintenanceDays:-30}"
    [[ "$interval" =~ ^[1-9][0-9]*$ ]] || { echo 'Lyrics: invalid maintenance interval'; exit 2; }
    python3 "$helper" --maintenance "${lyricsLibraryRoot:-/music}" --maintenance-days "$interval"
    exit $?
elif [ "${1:-}" = --status ]; then
    python3 "$helper" --status
elif [ "${1:-}" = --retry ]; then
    python3 "$helper" --retry "${2:?Job ID required}"
elif [ "${1:-}" = --work ]; then
    budget="${lyricsTimeout:-${beetsLyricsTimeout:-3m}}"
    if [[ ! "$budget" =~ ^[1-9][0-9]*[smhd]?$ ]]; then
        echo "Lyrics: invalid worker timeout; skipped"
        exit 0
    fi
    timeout -k 2s "$budget" python3 "$helper" --work
    status=$?
    [ "$status" = 0 ] || echo "Lyrics: worker interrupted/failed ($status); queued work retained"
else
    timeout -k 1s 15s python3 "$helper" --enqueue "$1" --album-id "${2:-}"
    status=$?
    [ "$status" = 0 ] || echo "Lyrics: local enqueue failed ($status); import continues"
fi
exit 0
