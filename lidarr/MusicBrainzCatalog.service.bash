#!/usr/bin/env bash
# Scan only a small rotating batch; MusicBrainz editor submissions always remain manual.
while true; do
    if python3 /config/extended/python/MusicBrainzCatalog.py >> /config/logs/musicbrainz-catalog.log 2>&1; then
        sleep 24h
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') :: MusicBrainz catalog scan failed; retrying in 5 minutes" >> /config/logs/musicbrainz-catalog.log
        sleep 5m
    fi
done
