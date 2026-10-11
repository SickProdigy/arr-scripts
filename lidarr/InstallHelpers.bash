#!/usr/bin/env bash
# Install versioned project dependencies, following the Beets installation model.
set -euo pipefail
repositoryBase="${ARR_HELPER_REPOSITORY_BASE:-https://github.com/SickProdigy}"
# These are release pins, not moving branches. Publish both releases before deployment.
sonicratingsSource="${ARR_SONICRATINGS_ENGINE_SOURCE:-git+${repositoryBase%/}/SonicRatings.git@engine-v0.1.0}"
musicbrainzSource="${ARR_MUSICBRAINZ_HELPER_SOURCE:-git+${repositoryBase%/}/musicbrainz-helper.git@v0.3.0}"
# requests and mutagen are already installed with Lidarr's normal dependencies.
# Install both internal packages explicitly; do not resolve their names on PyPI.
uv pip install --system --break-system-packages --no-deps "$sonicratingsSource" "$musicbrainzSource"
python3 - <<'PY'
from importlib.metadata import version
expected = {"sonicratings-engine": "0.1.0", "musicbrainz-helper": "0.3.0"}
for package, wanted in expected.items():
    if version(package) != wanted:
        raise SystemExit(f"Unexpected {package} version; expected {wanted}")
from musicbrainz_helper import musicbrainz_genres, ratings_helper
from sonicratings_engine import ratings
print("Installed MusicBrainz helper 0.3.0 and SonicRatings engine 0.1.0")
PY
command -v musicbrainz-genres >/dev/null
command -v musicbrainz-ratings >/dev/null
