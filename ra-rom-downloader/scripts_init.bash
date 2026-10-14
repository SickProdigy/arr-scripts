#!/usr/bin/with-contenv bash
set -o pipefail
export arrScriptsRawBase="${ARR_SCRIPTS_RAW_BASE:-https://raw.githubusercontent.com/SickProdigy/arr-scripts/main}"
curl -fsSL "${arrScriptsRawBase%/}/ra-rom-downloader/setup.bash" | bash
exit
