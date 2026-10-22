#!/usr/bin/env bash
# Install versioned project dependencies, following the Beets installation model.
set -euo pipefail
# Install published versions; explicit sources support local package validation.
musicbrainzSource="${ARR_MUSICBRAINZ_HELPER_SOURCE:-musicbrainz-helper==0.4.0}"
sonicratingsSource="${ARR_SONICRATINGS_SOURCE:-sonicratings==1.4.0}"
# All package dependencies are installed explicitly by Lidarr setup.
uv pip install --system --break-system-packages --no-deps "$musicbrainzSource" "$sonicratingsSource"
python3 - <<'PY'
from importlib.metadata import version
expected = {"musicbrainz-helper": "0.4.0", "sonicratings": "1.4.0"}
for package, wanted in expected.items():
    if version(package) != wanted:
        raise SystemExit(f"Unexpected {package} version; expected {wanted}")
from musicbrainz_helper import musicbrainz_genres, ratings, ratings_worker
from sonicratings import cli, automation
print("Installed musicbrainz-helper 0.4.0 and SonicRatings 1.4.0")
PY
command -v musicbrainz-genres >/dev/null
command -v musicbrainz-ratings >/dev/null
command -v sonicratings >/dev/null
# Optional maintenance remains disabled until a published pin is configured.
if [ -f "${ARR_EXTENDED_CONF:-/config/extended.conf}" ]; then
    set +u
    source "${ARR_EXTENDED_CONF:-/config/extended.conf}"
    set -u
fi
if [ "${enableLibraryMaintenance:-false}" = true ]; then
    pin="${navidromeScriptsVersion:-}"
    [[ "$pin" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Set navidromeScriptsVersion to a released version before enabling library maintenance'; exit 1; }
    sourceSpec="${ARR_NAVIDROME_SCRIPTS_SOURCE:-navidrome-scripts[tags]==$pin}"
    uv pip install --system --break-system-packages --no-deps "$sourceSpec" 'mediafile>=0.17'
    python3 - "$pin" <<'PY'
from importlib.metadata import version
import sys
if version('navidrome-scripts') != sys.argv[1]:
    raise SystemExit('Unexpected navidrome-scripts package version')
from navidrome_scripts import maintenance
from mediafile import MediaFile
PY
    command -v navidrome-maintenance >/dev/null
fi
