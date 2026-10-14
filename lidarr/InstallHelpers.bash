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
