#!/usr/bin/env bash
# One serial worker keeps MusicBrainz rate limiting and the submission ledger shared.
while true; do
    bash /config/extended/MusicBrainzGenres.bash --work
    sleep 60
done
