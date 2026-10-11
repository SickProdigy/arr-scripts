#!/usr/bin/env bash
# Optional bounded phase; failures must never fail an otherwise valid import.
source /config/extended.conf
[ "${enableBeetsLyrics:-true}" = "true" ] || exit 0
budget="${beetsLyricsTimeout:-3m}"
if [[ ! "$budget" =~ ^[1-9][0-9]*[smhd]?$ ]]; then
    echo "Beets lyrics: invalid timeout; skipped" >&2
    exit 0
fi
started=$SECONDS
echo "Beets lyrics: starting (budget $budget)"
timeout -k 1s "$budget" python3 /config/extended/python/BeetsLyrics.py \
    --config "$1" --library "$2" --directory "$3"
status=$?
if [ "$status" = 124 ] || [ "$status" = 137 ]; then
    echo "Beets lyrics: timed out after $((SECONDS-started))s; completed lyrics retained; import continues"
else
    echo "Beets lyrics: finished in $((SECONDS-started))s, exit status $status; import continues"
fi
exit 0
