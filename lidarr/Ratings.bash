#!/usr/bin/env bash
# Import callbacks only enqueue; the service rates and synchronizes asynchronously.
source /config/extended.conf
[ "${lidarr_eventtype:-}" = "Test" ] && exit 0
[ "${enableRatings:-false}" = "true" ] || exit 0
helper="musicbrainz-ratings"
if ! command -v "$helper" >/dev/null; then
    echo "Ratings: helper missing" >&2
    case "${1:-}" in
        --work|--library) exit 1 ;;
        *) exit 0 ;;
    esac
fi
export SUBSONIC_URL="${subsonicUrl:-${navidromeUrl:-}}"
export SUBSONIC_USERNAME="${subsonicUsername:-${navidromeUsername:-}}"
export SUBSONIC_PASSWORD="${subsonicPassword:-${navidromePassword:-}}"
export LASTFM_API_KEY="${lastfmApiKey:-}"
export MB_USERNAME="${musicBrainzUsername:-}" MB_TOKEN="${musicBrainzToken:-}"
export RATINGS_SUBMIT="${enableMusicBrainzHelper:-false}"
export RATINGS_INTERVAL="${ratingsScriptInterval:-15m}"
if [ "${1:-}" = "--work" ]; then
    exec "$helper" work --state-dir /config/extended/ratings-helper
fi
if [ "${1:-}" = "--library" ]; then
    exec "$helper" library --state-dir /config/extended/ratings-helper
fi
if [ "${enableBeetsTagging:-false}" = "true" ] && [ "${1:-}" != "--after-beets" ] && \
   [ "${lidarr_eventtype:-}" != "TrackRetag" ]; then
    exit 0
fi
album="${lidarr_album_id:-${2:-}}"
[[ "$album" =~ ^[1-9][0-9]*$ ]] || exit 0
source /config/extended/functions
getArrAppInfo
export LIDARR_URL="$arrUrl" LIDARR_API_KEY="$arrApiKey"
"$helper" enqueue --state-dir /config/extended/ratings-helper --album-id "$album" || \
    echo "Ratings: queue failed; media import remains successful" >&2
exit 0
