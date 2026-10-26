#!/usr/bin/env bash
# Scan only a small rotating batch; MusicBrainz editor submissions always remain manual.
while true; do
    python3 /config/extended/python/MusicBrainzCatalog.py >> /config/logs/musicbrainz-catalog.log 2>&1
    sleep 24h
done
