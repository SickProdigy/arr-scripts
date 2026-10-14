#!/usr/bin/with-contenv bash
export arrScriptsRawBase="${ARR_SCRIPTS_RAW_BASE:-https://raw.githubusercontent.com/SickProdigy/arr-scripts/main}"
echo "************ install and update packages ************"
apk add -U --update --no-cache \
  jq \
  xq \
  py3-pip \
  ffmpeg
echo "************ install python packages ************"
pip install --upgrade --no-cache-dir -U --break-system-packages yq

mkdir -p /custom-services.d
echo "Download AutoConfig service..."
curl "${arrScriptsRawBase%/}/readarr/AutoConfig.bash" -o /custom-services.d/AutoConfig
echo "Done"

echo "Download QueueCleaner service..."
curl "${arrScriptsRawBase%/}/universal/services/QueueCleaner" -o /custom-services.d/QueueCleaner
echo "Done"

mkdir -p /config/extended
echo "Download Script Functions..."
curl "${arrScriptsRawBase%/}/universal/functions.bash" -o /config/extended/functions
echo "Done"

echo "Download PlexNotify script..."
curl "${arrScriptsRawBase%/}/readarr/PlexNotify.bash" -o /config/extended/PlexNotify.bash
echo "Done"

echo "Download combine script..."
curl "${arrScriptsRawBase%/}/readarr/combine.bash" -o /config/extended/combine.bash
echo "Done"

echo "Download AutoConfig config..."
curl "${arrScriptsRawBase%/}/readarr/AutoConfig.json" -o /config/extended/AutoConfig.json
echo "Done"

chmod 777 -R /config/extended

if [ ! -f /config/extended.conf ]; then
	echo "Download Extended config..."
	curl "${arrScriptsRawBase%/}/readarr/extended.conf" -o /config/extended.conf
	chmod 777 /config/extended.conf
	echo "Done"
fi

if [ -f /custom-services.d/scripts_init.bash ]; then
   # user misconfiguration detected, sleeping...
   sleep infinity
fi
exit
