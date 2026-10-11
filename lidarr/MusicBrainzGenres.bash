#!/usr/bin/env bash
# Queue locally tagged files; MusicBrainz requests run in the separate worker.
source /config/extended.conf

if [ "${lidarr_eventtype:-}" = "Test" ]; then
    exit 0
fi
if [ "${enableMusicBrainzHelper:-false}" != "true" ]; then
    exit 0
fi

helper="musicbrainz-genres"
queue="/config/extended/musicbrainz-helper"
export MB_USERNAME="${musicBrainzUsername:-}" MB_TOKEN="${musicBrainzToken:-}"
profileArguments=(--account-profile)
if ! command -v "$helper" >/dev/null; then
    echo "MusicBrainz genres: installed command $helper missing; run setup" >&2
    # Contribution failures must not fail a successful media import.
    case "${1:-}" in
        --work|--library) exit 1 ;;
        *) exit 0 ;;
    esac
fi

if [ "${1:-}" = "--work" ]; then
    arguments=(work --queue-dir "$queue" "${profileArguments[@]}")
    arguments+=(--entity artist --entity release-group --entity release --entity recording)
    if [ "${2:-}" != "--preview" ]; then
        # Credentials authorize contributions to this account; never submit anonymously.
        if [ -z "$MB_USERNAME" ] || [ -z "$MB_TOKEN" ]; then
            exit 0
        fi
        arguments+=(--submit)
    fi
    exec "$helper" "${arguments[@]}"
fi

# When Beets is enabled it queues after tagging, avoiding notification-order races.
if [ "${enableBeetsTagging:-false}" = "true" ] && [ "${1:-}" != "--after-beets" ] && \
   [ "${1:-}" != "--library" ] && [ "${lidarr_eventtype:-}" != "TrackRetag" ]; then
    exit 0
fi
album="${lidarr_album_id:-${2:-}}"
if [ "${1:-}" != "--library" ] && [[ ! "$album" =~ ^[1-9][0-9]*$ ]]; then
    echo "MusicBrainz genres: missing or invalid Lidarr album ID; skipped" >&2
    exit 0
fi
source /config/extended/functions
getArrAppInfo
export LIDARR_URL="$arrUrl" LIDARR_API_KEY="$arrApiKey"
genreSource="embedded-provider"
[ "${enableBeetsTagging:-false}" = "true" ] && genreSource="embedded-beets"
[ "${1:-}" = "--after-beets" ] && genreSource="embedded-beets"
if [ "${1:-}" = "--library" ]; then
    # Explicit batch command: retain failure exit status so incomplete exports are visible.
    exec "$helper" scan --queue-dir "$queue" "${profileArguments[@]}" --source "$genreSource"
fi
"$helper" enqueue --queue-dir "$queue" "${profileArguments[@]}" --album-id "$album" --source "$genreSource" || \
    echo "MusicBrainz genres: queue failed; media import remains successful" >&2
exit 0
