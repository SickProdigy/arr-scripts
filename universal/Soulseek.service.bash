#!/usr/bin/env bash
export ARR_SOULSEEK_APP="__APP__"
while true; do
    bash /config/extended/Soulseek.bash || echo "Soulseek: worker failed; retrying next minute"
    sleep 60
done
