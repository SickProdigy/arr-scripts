#!/usr/bin/with-contenv bash
source /config/extended.conf
if [ "${enableDashboard:-false}" != true ]; then
    exec sleep infinity
fi
if [ "$(id -u)" = 0 ] && command -v s6-setuidgid >/dev/null; then
    exec s6-setuidgid abc python3 /config/extended/dashboard.py --app __APP__
fi
exec python3 /config/extended/dashboard.py --app __APP__
