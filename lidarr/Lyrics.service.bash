#!/usr/bin/env bash
while true; do
    bash /config/extended/Lyrics.bash --work >> /config/logs/lyrics.log 2>&1
    sleep 30
done
