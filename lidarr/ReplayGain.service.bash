#!/usr/bin/env bash
while true; do
    bash /config/extended/ReplayGainTagger.bash --maintenance >> /config/logs/replaygain-maintenance.log 2>&1
    sleep 3600
done
