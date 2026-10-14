#!/usr/bin/env bash
# Queue import identities locally; independent packages do remote work in the service.
source /config/extended.conf
[ "${lidarr_eventtype:-}" = "Test" ] && exit 0
[ "${enableRatings:-false}" = "true" ] || [ "${enableMusicBrainzHelper:-false}" = "true" ] || exit 0
export SUBSONIC_URL="${subsonicUrl:-${navidromeUrl:-}}"
export SUBSONIC_USERNAME="${subsonicUsername:-${navidromeUsername:-}}"
export SUBSONIC_PASSWORD="${subsonicPassword:-${navidromePassword:-}}"
# Genre-only installations do not need a Subsonic account.
if [ -z "$SUBSONIC_URL" ] || [ -z "$SUBSONIC_USERNAME" ] || [ -z "$SUBSONIC_PASSWORD" ]; then
    if [ "${enableRatings:-false}" = "true" ]; then
        echo "Ratings: configure the Subsonic URL, username, and password" >&2
        case "${1:-}" in --work|--library) exit 1 ;; esac
    fi
    exit 0
fi
export NAV_BASE_URL="$SUBSONIC_URL" NAV_USER="$SUBSONIC_USERNAME" NAV_PASS="$SUBSONIC_PASSWORD"
export LASTFM_API_KEY="${lastfmApiKey:-}"
export SPOTIFY_CLIENT_ID="${spotifyClientId:-}" SPOTIFY_CLIENT_SECRET="${spotifyClientSecret:-}"
export TIDAL_CLIENT_ID="${tidalClientId:-}" TIDAL_CLIENT_SECRET="${tidalClientSecret:-}" TIDAL_COUNTRY_CODE="${tidalCountryCode:-US}"
export DISCOGS_TOKEN="${discogsToken:-}"
export RATINGS_SOURCE="${ratingsSource:-subsonic}"
export MB_USERNAME="${musicBrainzUsername:-}" MB_TOKEN="${musicBrainzToken:-}"
export RATINGS_SUBMIT="${enableMusicBrainzHelper:-false}"
export RATINGS_INTERVAL="${ratingsScriptInterval:-15m}"
export RATINGS_GENERATED_RATINGS_DIR=/config/extended/sonicratings
export MUSICBRAINZ_REQUEST_LOCK_DIR=/config/extended/musicbrainz-helper
provider="${ratingsProvider:-}"
[ -n "$provider" ] || { if [ -n "$LASTFM_API_KEY" ]; then provider=lastfm; else provider=musicbrainz; fi; }
sonicArgs=(--state-dir /config/extended/sonicratings --provider "$provider")
[ "${discogsInheritAlbumRating:-false}" != "true" ] || sonicArgs+=(--discogs-inherit-album-rating)
if [ "${2:-}" = "--preview" ]; then
    sonicArgs+=(--preview)
    export RATINGS_SUBMIT=false
fi
status=0
runHelper() {
    local helper="$1"
    shift
    if ! command -v "$helper" >/dev/null; then
        echo "Ratings: $helper missing; run the package installer" >&2
        status=1
        return
    fi
    "$helper" "$@" || status=1
}
contribute() {
    [ "${enableMusicBrainzHelper:-false}" = "true" ] && [ -n "$MB_USERNAME" ] && [ -n "$MB_TOKEN" ]
}
if [ "${1:-}" = "--work" ] || [ "${1:-}" = "--library" ]; then
    command=work
    [ "$1" != "--library" ] || command=library
    if [ "${enableRatings:-false}" = "true" ]; then
        runHelper sonicratings "$command" "${sonicArgs[@]}"
    fi
    if contribute; then
        runHelper musicbrainz-ratings "$command" --state-dir /config/extended/ratings-helper
    fi
    exit "$status"
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
queueHelper() {
    local output result
    output=$("$@" 2>&1)
    result=$?
    if [ "$result" = "0" ]; then
        [ -z "$output" ] || printf '%s\n' "$output"
    else
        [ -z "$output" ] || printf '%s\n' "$output" >&2
        echo "Ratings: queue failed; media import remains successful" >&2
    fi
}
if [ "${enableRatings:-false}" = "true" ]; then
    queueHelper sonicratings enqueue "${sonicArgs[@]}" --album-id "$album"
fi
if contribute; then
    queueHelper musicbrainz-ratings enqueue --state-dir /config/extended/ratings-helper --album-id "$album"
fi
exit 0
