#!/usr/bin/env bash
scriptVersion="1.3"
scriptName="ArtworkExtractor"

#### Import Settings
source /config/extended.conf
#### Import Functions
source /config/extended/functions
#### Create Log File
logfileSetup

SECONDS=0

if [ "$lidarr_eventtype" == "Test" ]; then
	log "Tested Successfully"
	exit 0	
fi

getArrAppInfo
verifyApiAccess

if [ -z "$lidarr_album_id" ]; then
	lidarr_album_id="$1"
fi



getAlbumArtist="$(curl -s "$arrUrl/api/v1/album/$lidarr_album_id" -H "X-Api-Key: ${arrApiKey}" | jq -r .artist.artistName)"
getAlbumArtistPath="$(curl -s "$arrUrl/api/v1/album/$lidarr_album_id" -H "X-Api-Key: ${arrApiKey}" | jq -r .artist.path)"
getTrackPath="$(curl -s "$arrUrl/api/v1/trackFile?albumId=$lidarr_album_id" -H "X-Api-Key: ${arrApiKey}" | jq -r '.[0].path // empty')"
getFolderPath="$(dirname "$getTrackPath")"
getAlbumFolderName="$(basename "$getFolderPath")"

log "Processing :: $getAlbumFolderName :: Processing Files..."

if echo "$getFolderPath" | grep "$getAlbumArtistPath" | read; then
	if [ ! -d "$getFolderPath" ]; then
		log "ERROR :: \"$getFolderPath\" Folder is missing :: Exiting..."
	fi
else 
	log "ERROR :: $getAlbumArtistPath not found within \"$getFolderPath\" :: Exiting..."
	exit
fi

# Preserve existing covers. Convert older artwork names to cover.jpg without
# removing the original, then fall back to artwork embedded in an audio file.
coverPath="$getFolderPath/cover.jpg"
if [ -s "$coverPath" ] || [ -s "$getFolderPath/cover.jpeg" ] || [ -s "$getFolderPath/cover.png" ]; then
    log "Processing :: $getAlbumFolderName :: Album artwork already exists"
    exit 0
fi

artworkSource=""
for candidate in folder.jpg folder.jpeg folder.png coverart.jpg coverart.jpeg coverart.png album.jpg; do
    if [ -s "$getFolderPath/$candidate" ]; then
        artworkSource="$getFolderPath/$candidate"
        break
    fi
done

extract_cover () {
    local stagedCover result=1
    stagedCover="$(mktemp "$getFolderPath/.arr-cover-XXXXXX.jpg")" || return 1
    if ffmpeg -nostdin -y -loglevel error -i "$1" -map 0:v:0 -frames:v 1 "$stagedCover" >/dev/null 2>&1 && [ -s "$stagedCover" ]; then
        mv -n -- "$stagedCover" "$coverPath"
        [ -s "$coverPath" ] && result=0
    fi
    rm -f -- "$stagedCover"
    return "$result"
}

if [ -n "$artworkSource" ]; then
    extract_cover "$artworkSource"
else
    while IFS= read -r -d '' file; do
        log "Processing :: $getAlbumFolderName :: $(basename -- "$file") :: Extracting artwork to cover.jpg"
        if extract_cover "$file"; then
            break
        fi
    done < <(find "$getFolderPath" -type f \( -iname '*.flac' -o -iname '*.opus' -o -iname '*.m4a' -o -iname '*.mp3' \) -print0)
fi

if [ -s "$coverPath" ]; then
    chmod 666 "$coverPath"
    log "Processing :: $getAlbumFolderName :: Album artwork saved to: $coverPath"
else
    log "Processing :: $getAlbumFolderName :: No album artwork extracted"
fi

duration=$SECONDS
log "Processing :: $getAlbumFolderName :: Finished in $(($duration / 60 )) minutes and $(($duration % 60 )) seconds!"
exit
