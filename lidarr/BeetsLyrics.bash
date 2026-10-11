#!/usr/bin/env bash
# Compatibility entry point for already-running older Audio/BeetsTagger scripts.
# Old arguments: Beets config, Beets database, processed directory.
bash /config/extended/Lyrics.bash "${3:?Processed directory required}" "${wantedAlbumId:-${lidarr_album_id:-}}"
exit 0
