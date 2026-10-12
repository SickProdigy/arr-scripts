#!/usr/bin/env bash
scriptVersion="1.1"
scriptName="PlexNotify"

#### Import Settings
source /config/extended.conf
#### Import Functions
source /config/extended/functions
#### Create Log File
logfileSetup

if [ -z "$lidarr_artist_path" ]; then
	lidarr_artist_path="$1"
	notfidedBy=Extended_Script
else
	notfidedBy=Lidarr
fi
lidarrRootFolderPath="$(dirname "$lidarr_artist_path")"


if [ "$lidarr_eventtype" == "Test" ]; then
	log "Tested Successfully"
	exit 0	
fi

plexConnectionError () {
	log "ERROR :: Cannot communicate with Plex"
	log "ERROR :: Please check your plexUrl and plexToken"
	log "ERROR :: Configured plexUrl \"$plexUrl\""
	log "ERROR :: Configured plexToken is $([ -n "$plexToken" ] && echo 'set' || echo 'empty')"
	log "ERROR :: Exiting..."
	exit
}

# Validate connection
plexIdentity="$(curl -fsS "$plexUrl/?X-Plex-Token=$plexToken")" || plexConnectionError
plexVersion="$(python3 -c '
import sys
import xml.etree.ElementTree as ET
print(ET.fromstring(sys.stdin.read()).get("version", ""))
' <<< "$plexIdentity" 2>/dev/null)" || plexConnectionError
if [ -z "$plexVersion" ]; then
	plexConnectionError
fi
log "Plex Connection Established, version: $plexVersion"

plexLibraries="$(curl -fsS "$plexUrl/library/sections?X-Plex-Token=$plexToken")" || plexConnectionError
plexLibraryData="$(python3 -c '
import json
import sys
import xml.etree.ElementTree as ET

root = ET.fromstring(sys.stdin.read())
libraries = [
    {"key": directory.get("key", ""), "paths": [location.get("path", "") for location in directory.findall("Location")]}
    for directory in root.findall("Directory")
    if directory.get("type") == "artist"
]
json.dump(libraries, sys.stdout)
' <<< "$plexLibraries" 2>/dev/null)" || plexConnectionError
mapfile -t plexKeys < <(jq -r '.[].key' <<< "$plexLibraryData")
if [ "${#plexKeys[@]}" -eq 0 ]; then
	log "ERROR: No Plex Music Type libraries found"
	log "ERROR: Exiting..."
	exit 1
fi

if ! jq -e --arg path "$lidarrRootFolderPath" 'any(.[]; .paths | index($path))' <<< "$plexLibraryData" >/dev/null; then
	log "ERROR: No Plex Library found containing path \"$lidarrRootFolderPath\""
	log "ERROR: Add \"$lidarrRootFolderPath\" as a folder to a Plex Music Library"
	exit 1
fi

for plexKey in "${plexKeys[@]}"; do
	if jq -e --arg key "$plexKey" --arg path "$lidarrRootFolderPath" 'any(.[]; .key == $key and (.paths | index($path)))' <<< "$plexLibraryData" >/dev/null; then
		plexFolderEncoded="$(jq -R -r @uri <<<"$lidarr_artist_path")"
		curl -s "$plexUrl/library/sections/$plexKey/refresh?path=$plexFolderEncoded&X-Plex-Token=$plexToken"
		log  "Plex Scan notification sent! ($plexKey :: $lidarr_artist_path)"
	fi
done

exit
