#!/usr/bin/env bash
while true; do
    bash /config/extended/Lyrics.bash --maintenance >> /config/logs/lyrics-maintenance.log 2>&1
    sleep 3600
done
