#!/usr/bin/env bash
export ARR_SOULSEEK_APP="__APP__"
mkdir -p /config/logs
echo "$(date '+%Y-%m-%d %H:%M:%S') :: Soulseek ${ARR_SOULSEEK_APP} service starting" >> /config/logs/Soulseek.log
while true; do
    bash /config/extended/Soulseek.bash || echo "$(date '+%Y-%m-%d %H:%M:%S') :: Soulseek: worker failed; retrying next minute" | tee -a /config/logs/Soulseek.log
    sleep 60
done
