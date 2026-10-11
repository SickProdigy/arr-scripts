#!/usr/bin/env bash
scriptVersion="3.2"
scriptName="AutoConfig"

### Import Settings
source /config/extended.conf
#### Import Functions
source /config/extended/functions

logfileSetup

if [ "$enableAutoConfig" != "true" ]; then
	log "Script is not enabled, enable by setting enableAutoConfig to \"true\" by modifying the \"/config/extended.conf\" config file..."
	log "Sleeping (infinity)"
	sleep infinity
fi


getArrAppInfo
verifyApiAccess

if [ "$configureMediaManagement" == "true" ] || [ -z "$configureMediaManagement" ]; then
  log "Configuring Lidarr Media Management Settings"
  postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/config/mediamanagement" -X PUT -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"autoUnmonitorPreviouslyDownloadedTracks":false,"recycleBin":"","recycleBinCleanupDays":7,"downloadPropersAndRepacks":"preferAndUpgrade","createEmptyArtistFolders":false,"deleteEmptyFolders":true,"fileDate":"albumReleaseDate","watchLibraryForChanges":false,"rescanAfterRefresh":"always","allowFingerprinting":"newFiles","setPermissionsLinux":false,"chmodFolder":"777","chownGroup":"","skipFreeSpaceCheckWhenImporting":false,"minimumFreeSpaceWhenImporting":100,"copyUsingHardlinks":true,"importExtraFiles":true,"extraFileExtensions":"jpg,png,lrc","id":1}')
fi

if [ "$configureMetadataConsumerSettings" == "true" ] || [ -z "$configureMetadataConsumerSettings" ]; then
  log "Configuring Lidarr Metadata ConsumerSettings"
  postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/metadata/1?" -X PUT -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"enable":true,"name":"Kodi (XBMC) / Emby","fields":[{"name":"artistMetadata","value":true},{"name":"albumMetadata","value":true},{"name":"artistImages","value":true},{"name":"albumImages","value":true}],"implementationName":"Kodi (XBMC) / Emby","implementation":"XbmcMetadata","configContract":"XbmcMetadataSettings","infoLink":"https://wiki.servarr.com/lidarr/supported#xbmcmetadata","tags":[],"id":1}')
fi

if [ "$configureMetadataProviderSettings" == "true" ] || [ -z "$configureMetadataProviderSettings" ]; then
  log "Configuring Lidarr Metadata Provider Settings"
  postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/config/metadataProvider" -X PUT -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"metadataSource":"","writeAudioTags":"newFiles","scrubAudioTags":false,"id":1}')
fi

if [ "$configureCustomScripts" == "true" ] || [ -z "$configureCustomScripts" ]; then
	log "Configuring Lidarr Custom Scripts"
	notificationData=$(curl -s "$arrUrl/api/v1/notification" -H "X-Api-Key: ${arrApiKey}")
	genreNotificationIds=$(echo "$notificationData" | jq -r '.[] | select(.name=="MusicBrainzGenres.bash") | .id')
	if [ "${enableMusicBrainzHelper:-false}" != "true" ]; then
		for genreNotificationId in $genreNotificationIds; do
			curl -s "$arrUrl/api/v1/notification/$genreNotificationId" -X DELETE -H "X-Api-Key: ${arrApiKey}" >/dev/null
		done
	else
		log "Configuring MusicBrainzGenres.bash for imports, upgrades and retags"
		genreNotification=$(jq -n '{name:"MusicBrainzGenres.bash", implementation:"CustomScript",
			implementationName:"Custom Script", configContract:"CustomScriptSettings",
			onGrab:false, onReleaseImport:true, onUpgrade:true, onRename:false,
			onHealthIssue:false, onDownloadFailure:false, onImportFailure:false,
			onTrackRetag:true, onApplicationUpdate:false,
			fields:[{name:"path",value:"/config/extended/MusicBrainzGenres.bash"},{name:"arguments",value:""}],tags:[]}')
		genreNotificationMethod="POST"
		genreNotificationEndpoint="$arrUrl/api/v1/notification"
		genreNotificationId=$(echo "$genreNotificationIds" | head -n 1)
		if [ -n "$genreNotificationId" ]; then
			genreNotificationMethod="PUT"
			genreNotificationEndpoint="$genreNotificationEndpoint/$genreNotificationId"
			genreNotification=$(echo "$genreNotification" | jq --argjson id "$genreNotificationId" '. + {id:$id}')
		fi
		curl -sf "$genreNotificationEndpoint" -X "$genreNotificationMethod" -H 'Content-Type: application/json' \
			-H "X-Api-Key: ${arrApiKey}" --data "$genreNotification" >/dev/null || log "MusicBrainz genre notification registration failed"
	fi
	plexNotificationId=$(echo "$notificationData" | jq -r '.[] | select(.name=="PlexNotify.bash") | .id' | head -n 1)
	ratingsNotificationIds=$(echo "$notificationData" | jq -r '.[] | select(.name=="Ratings.bash") | .id')
	if [ "${enableRatings:-false}" != "true" ]; then
		for ratingsNotificationId in $ratingsNotificationIds; do
			curl -s "$arrUrl/api/v1/notification/$ratingsNotificationId" -X DELETE -H "X-Api-Key: ${arrApiKey}" >/dev/null
		done
	else
		log "Configuring Ratings.bash for imports, upgrades and retags"
		ratingsNotification=$(jq -n '{name:"Ratings.bash", implementation:"CustomScript",
			implementationName:"Custom Script", configContract:"CustomScriptSettings",
			onGrab:false, onReleaseImport:true, onUpgrade:true, onRename:false,
			onHealthIssue:false, onDownloadFailure:false, onImportFailure:false,
			onTrackRetag:true, onApplicationUpdate:false,
			fields:[{name:"path",value:"/config/extended/Ratings.bash"},{name:"arguments",value:""}],tags:[]}')
		ratingsNotificationMethod="POST"
		ratingsNotificationEndpoint="$arrUrl/api/v1/notification"
		ratingsNotificationId=$(echo "$ratingsNotificationIds" | head -n 1)
		if [ -n "$ratingsNotificationId" ]; then
			ratingsNotificationMethod="PUT"
			ratingsNotificationEndpoint="$ratingsNotificationEndpoint/$ratingsNotificationId"
			ratingsNotification=$(echo "$ratingsNotification" | jq --argjson id "$ratingsNotificationId" '. + {id:$id}')
		fi
		curl -sf "$ratingsNotificationEndpoint" -X "$ratingsNotificationMethod" -H 'Content-Type: application/json' \
			-H "X-Api-Key: ${arrApiKey}" --data "$ratingsNotification" >/dev/null || log "Rating notification registration failed"
	fi
	if [ "$enablePlexNotify" != "true" ] || [ -z "$plexUrl" ] || [ -z "$plexToken" ]; then
		if [ "$enablePlexNotify" == "true" ]; then
			log "PlexNotify.bash is enabled but plexUrl or plexToken is empty; skipping registration"
		else
			log "PlexNotify.bash is disabled"
		fi
		if [ -n "$plexNotificationId" ]; then
			curl -s "$arrUrl/api/v1/notification/$plexNotificationId" -X DELETE -H "X-Api-Key: ${arrApiKey}" >/dev/null
			log "Removed disabled or incomplete PlexNotify.bash custom script"
		fi
	elif [ -n "$plexNotificationId" ]; then
		log "PlexNotify.bash Already added to Lidarr custom scripts"
	else
		log "Adding PlexNotify.bash to Lidarr custom scripts"
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/filesystem?path=%2Fconfig%2Fextended%2FPlexNotify.bash&allowFoldersWithoutTrailingSlashes=true&includeFiles=true" -H "X-Api-Key: ${arrApiKey}")
	
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/notification?" -X POST -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"onGrab":false,"onReleaseImport":true,"onUpgrade":true,"onRename":true,"onHealthIssue":false,"onDownloadFailure":false,"onImportFailure":false,"onTrackRetag":false,"onApplicationUpdate":false,"supportsOnGrab":true,"supportsOnReleaseImport":true,"supportsOnUpgrade":true,"supportsOnRename":true,"supportsOnHealthIssue":true,"includeHealthWarnings":false,"supportsOnDownloadFailure":false,"supportsOnImportFailure":false,"supportsOnTrackRetag":true,"supportsOnApplicationUpdate":true,"name":"PlexNotify.bash","fields":[{"name":"path","value":"/config/extended/PlexNotify.bash"},{"name":"arguments"}],"implementationName":"Custom Script","implementation":"CustomScript","configContract":"CustomScriptSettings","infoLink":"https://wiki.servarr.com/lidarr/supported#customscript","message":{"message":"Testing will execute the script with the EventType set to Test, ensure your script handles this correctly","type":"warning"},"tags":[]}')
	
	fi

 if curl -s "$arrUrl/api/v1/notification" -H "X-Api-Key: ${arrApiKey}" | jq -r .[].name | grep "LyricExtractor.bash" | read; then
		log "LyricExtractor.bash Already added to Lidarr custom scripts"
	else
		log "Adding LyricExtractor.bash to Lidarr custom scripts"
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/filesystem?path=%2Fconfig%2Fextended%2FLyricExtractor.bash&allowFoldersWithoutTrailingSlashes=true&includeFiles=true" -H "X-Api-Key: ${arrApiKey}")
	
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/notification?" -X POST -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"onGrab":false,"onReleaseImport":true,"onUpgrade":true,"onRename":true,"onHealthIssue":false,"onDownloadFailure":false,"onImportFailure":false,"onTrackRetag":false,"onApplicationUpdate":false,"supportsOnGrab":true,"supportsOnReleaseImport":true,"supportsOnUpgrade":true,"supportsOnRename":true,"supportsOnHealthIssue":true,"includeHealthWarnings":false,"supportsOnDownloadFailure":false,"supportsOnImportFailure":false,"supportsOnTrackRetag":true,"supportsOnApplicationUpdate":true,"name":"LyricExtractor.bash","fields":[{"name":"path","value":"/config/extended/LyricExtractor.bash"},{"name":"arguments"}],"implementationName":"Custom Script","implementation":"CustomScript","configContract":"CustomScriptSettings","infoLink":"https://wiki.servarr.com/lidarr/supported#customscript","message":{"message":"Testing will execute the script with the EventType set to Test, ensure your script handles this correctly","type":"warning"},"tags":[]}')
	
	fi

 	if curl -s "$arrUrl/api/v1/notification" -H "X-Api-Key: ${arrApiKey}" | jq -r .[].name | grep "ArtworkExtractor.bash" | read; then
		log "ArtworkExtractor.bash Already added to Lidarr custom scripts"
	else
		log "Adding ArtworkExtractor.bash to Lidarr custom scripts"
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/filesystem?path=%2Fconfig%2Fextended%2FArtworkExtractor.bash&allowFoldersWithoutTrailingSlashes=true&includeFiles=true" -H "X-Api-Key: ${arrApiKey}")
	
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/notification?" -X POST -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"onGrab":false,"onReleaseImport":true,"onUpgrade":true,"onRename":true,"onHealthIssue":false,"onDownloadFailure":false,"onImportFailure":false,"onTrackRetag":false,"onApplicationUpdate":false,"supportsOnGrab":true,"supportsOnReleaseImport":true,"supportsOnUpgrade":true,"supportsOnRename":true,"supportsOnHealthIssue":true,"includeHealthWarnings":false,"supportsOnDownloadFailure":false,"supportsOnImportFailure":false,"supportsOnTrackRetag":true,"supportsOnApplicationUpdate":true,"name":"ArtworkExtractor.bash","fields":[{"name":"path","value":"/config/extended/ArtworkExtractor.bash"},{"name":"arguments"}],"implementationName":"Custom Script","implementation":"CustomScript","configContract":"CustomScriptSettings","infoLink":"https://wiki.servarr.com/lidarr/supported#customscript","message":{"message":"Testing will execute the script with the EventType set to Test, ensure your script handles this correctly","type":"warning"},"tags":[]}')

	fi

 	if curl -s "$arrUrl/api/v1/notification" -H "X-Api-Key: ${arrApiKey}" | jq -r .[].name | grep "BeetsTagger.bash" | read; then
		log "BeetsTagger.bash Already added to Lidarr custom scripts"
	else
		log "Adding BeetsTagger.bash to Lidarr custom scripts"
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/filesystem?path=%2Fconfig%2Fextended%2FBeetsTagger.bash&allowFoldersWithoutTrailingSlashes=true&includeFiles=true" -H "X-Api-Key: ${arrApiKey}")
	
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/notification?" -X POST -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"onGrab":false,"onReleaseImport":true,"onUpgrade":true,"onRename":true,"onHealthIssue":false,"onDownloadFailure":false,"onImportFailure":false,"onTrackRetag":false,"onApplicationUpdate":false,"supportsOnGrab":true,"supportsOnReleaseImport":true,"supportsOnUpgrade":true,"supportsOnRename":true,"supportsOnHealthIssue":true,"includeHealthWarnings":false,"supportsOnDownloadFailure":false,"supportsOnImportFailure":false,"supportsOnTrackRetag":true,"supportsOnApplicationUpdate":true,"name":"BeetsTagger.bash","fields":[{"name":"path","value":"/config/extended/BeetsTagger.bash"},{"name":"arguments"}],"implementationName":"Custom Script","implementation":"CustomScript","configContract":"CustomScriptSettings","infoLink":"https://wiki.servarr.com/lidarr/supported#customscript","message":{"message":"Testing will execute the script with the EventType set to Test, ensure your script handles this correctly","type":"warning"},"tags":[]}')

	fi

	replayGainNotificationId=$(echo "$notificationData" | jq -r '.[] | select(.name=="ReplayGainTagger.bash") | .id' | head -n 1)
	if [ "$enableReplaygainTags" != "true" ]; then
		log "ReplayGainTagger.bash is disabled"
		if [ -n "$replayGainNotificationId" ]; then
			curl -s "$arrUrl/api/v1/notification/$replayGainNotificationId" -X DELETE -H "X-Api-Key: ${arrApiKey}" >/dev/null
			log "Removed disabled ReplayGainTagger.bash custom script"
		fi
	elif [ -n "$replayGainNotificationId" ]; then
		log "ReplayGainTagger.bash Already added to Lidarr custom scripts"
	else
		log "Adding ReplayGainTagger.bash to Lidarr custom scripts"
		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/filesystem?path=%2Fconfig%2Fextended%2FReplayGainTagger.bash&allowFoldersWithoutTrailingSlashes=true&includeFiles=true" -H "X-Api-Key: ${arrApiKey}")

		postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/notification?" -X POST -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"onGrab":false,"onReleaseImport":true,"onUpgrade":true,"onRename":false,"onHealthIssue":false,"onDownloadFailure":false,"onImportFailure":false,"onTrackRetag":false,"onApplicationUpdate":false,"supportsOnGrab":true,"supportsOnReleaseImport":true,"supportsOnUpgrade":true,"supportsOnRename":true,"supportsOnHealthIssue":true,"includeHealthWarnings":false,"supportsOnDownloadFailure":false,"supportsOnImportFailure":false,"supportsOnTrackRetag":true,"supportsOnApplicationUpdate":true,"name":"ReplayGainTagger.bash","fields":[{"name":"path","value":"/config/extended/ReplayGainTagger.bash"},{"name":"arguments"}],"implementationName":"Custom Script","implementation":"CustomScript","configContract":"CustomScriptSettings","infoLink":"https://wiki.servarr.com/lidarr/supported#customscript","message":{"message":"Testing will execute the script with the EventType set to Test, ensure your script handles this correctly","type":"warning"},"tags":[]}')

	fi
fi

if [ "$configureLidarrUiSettings" == "true" ] || [ -z "$configureLidarrUiSettings" ]; then
  log "Configuring Lidarr UI Settings"
  postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/config/ui" -X PUT -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"firstDayOfWeek":0,"calendarWeekColumnHeader":"ddd M/D","shortDateFormat":"MMM D YYYY","longDateFormat":"dddd, MMMM D YYYY","timeFormat":"h(:mm)a","showRelativeDates":true,"enableColorImpairedMode":true,"uiLanguage":1,"expandAlbumByDefault":true,"expandSingleByDefault":true,"expandEPByDefault":true,"expandBroadcastByDefault":true,"expandOtherByDefault":true,"theme":"auto","id":1}')
fi

if [ "$configureMetadataProfileSettings" == "true" ] || [ -z "$configureMetadataProfileSettings" ]; then
  log "Configuring Lidarr Standard Metadata Profile"
  postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/metadataprofile/1?" -X PUT -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"name":"Standard","primaryAlbumTypes":[{"albumType":{"id":2,"name":"Single"},"allowed":true},{"albumType":{"id":4,"name":"Other"},"allowed":true},{"albumType":{"id":1,"name":"EP"},"allowed":true},{"albumType":{"id":3,"name":"Broadcast"},"allowed":true},{"albumType":{"id":0,"name":"Album"},"allowed":true}],"secondaryAlbumTypes":[{"albumType":{"id":0,"name":"Studio"},"allowed":true},{"albumType":{"id":3,"name":"Spokenword"},"allowed":true},{"albumType":{"id":2,"name":"Soundtrack"},"allowed":true},{"albumType":{"id":7,"name":"Remix"},"allowed":true},{"albumType":{"id":9,"name":"Mixtape/Street"},"allowed":true},{"albumType":{"id":6,"name":"Live"},"allowed":false},{"albumType":{"id":4,"name":"Interview"},"allowed":false},{"albumType":{"id":8,"name":"DJ-mix"},"allowed":true},{"albumType":{"id":10,"name":"Demo"},"allowed":true},{"albumType":{"id":1,"name":"Compilation"},"allowed":true}],"releaseStatuses":[{"releaseStatus":{"id":3,"name":"Pseudo-Release"},"allowed":false},{"releaseStatus":{"id":1,"name":"Promotion"},"allowed":false},{"releaseStatus":{"id":0,"name":"Official"},"allowed":true},{"releaseStatus":{"id":2,"name":"Bootleg"},"allowed":false}],"id":1}')
fi


if [ "$configureTrackNamingSettings" == "true" ] || [ -z "$configureTrackNamingSettings" ]; then
  log "Configuring Lidarr Track Naming Settings"
  postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/config/naming" -X PUT -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"renameTracks":true,"replaceIllegalCharacters":true,"standardTrackFormat":"{Artist CleanName} - {Album Type} - {Release Year} - {Album CleanTitle}/{medium:00}{track:00} - {Track CleanTitle}","multiDiscTrackFormat":"{Artist CleanName} - {Album Type} - {Release Year} - {Album CleanTitle}/{medium:00}{track:00} - {Track CleanTitle}","artistFolderFormat":"{Artist CleanName}{ (Artist Disambiguation)}","includeArtistName":false,"includeAlbumTitle":false,"includeQuality":false,"replaceSpaces":false,"id":1}')
  postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/config/naming" -X PUT -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"renameTracks":true,"replaceIllegalCharacters":true,"standardTrackFormat":"{Artist CleanName} - {Album Type} - {Release Year} - {Album CleanTitle}/{medium:00}{track:00} - {Track CleanTitle}","multiDiscTrackFormat":"{Artist CleanName} - {Album Type} - {Release Year} - {Album CleanTitle}/{medium:00}{track:00} - {Track CleanTitle}","artistFolderFormat":"{Artist CleanName}{ (Artist Disambiguation)}","includeArtistName":false,"includeAlbumTitle":false,"includeQuality":false,"replaceSpaces":false,"id":1}')
  postSettingsToLidarr=$(curl -s "$arrUrl/api/v1/config/naming" -X PUT -H 'Content-Type: application/json' -H "X-Api-Key: ${arrApiKey}" --data-raw '{"renameTracks":true,"replaceIllegalCharacters":true,"standardTrackFormat":"{Artist CleanName} - {Album Type} - {Release Year} - {Album CleanTitle}/{medium:00}{track:00} - {Track CleanTitle}","multiDiscTrackFormat":"{Artist CleanName} - {Album Type} - {Release Year} - {Album CleanTitle}/{medium:00}{track:00} - {Track CleanTitle}","artistFolderFormat":"{Artist CleanName}{ (Artist Disambiguation)}","includeArtistName":false,"includeAlbumTitle":false,"includeQuality":false,"replaceSpaces":false,"id":1}')
fi


sleep infinity
exit $?
