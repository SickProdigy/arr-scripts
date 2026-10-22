#!/usr/bin/env bash
if [ "$(id -u)" = 0 ] && command -v s6-setuidgid >/dev/null; then
    exec s6-setuidgid abc bash "$0" "$@"
fi
while true; do
    bash /config/extended/LibraryMaintenance.bash --work >> /config/logs/library-maintenance.log 2>&1
    bash /config/extended/LibraryMaintenance.bash --audit >> /config/logs/library-maintenance.log 2>&1
    sleep 60
done
