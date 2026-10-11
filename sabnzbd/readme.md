# README

## Requirements

Container: [https://docs.linuxserver.io/images/docker-sabnzbd](https://docs.linuxserver.io/images/docker-sabnzbd)  

## Installation/setup

1. Add volume to your container
  `/custom-cont-init.d`
  Docker Run Example:
  `-v /path/to/preferred/local/directory:/custom-cont-init.d`
1. Download the [script_init.bash](https://github.com/SickProdigy/arr-scripts/blob/main/sabnzbd/scripts_init.bash) ([Download Link](https://raw.githubusercontent.com/SickProdigy/arr-scripts/main/sabnzbd/scripts_init.bash)) and place it into the following folder: `/custom-cont-init.d`
1. Start your container and wait for the application to load
1. Optional: Customize the configuration by modifying the following file `/config/extended.conf`
1. Add the `/config/scripts` folder to the "Scripts Folder" folder setting in SABnzbd
1. Add `video.bash` or `audio.bash` script to the appropriate SABnzbd category

## Updating

Updating is a bit more cumbersome. To update, do the following:

1. Download/update your local `/config/extended.conf` file with the latest options from: [extended.conf](https://github.com/SickProdigy/arr-scripts/blob/main/sabnzbd/extended.conf)
1. Restart the container, wait for it to fully load the application.
1. Restart the container again, for the new scripts to activate.

## Additional Information

For more details, visit the [Wiki](https://github.com/SickProdigy/arr-scripts/wiki)

## Using Tdarr for video processing

Set this in `/config/extended.conf`:

```bash
letTdarrHandleVideo="true"
```

Both `video.bash` (SMA) and the standalone `video2.bash` leave downloaded video,
audio/subtitle tracks, artwork, and sidecars untouched. They skip language-based
rejection, conversion, remuxing, metadata edits, and file cleanup. They still
require a supported video file to be present and finish the SABnzbd post-process
step so Sonarr/Radarr can import it. `video2.bash` also requests an Arr queue
refresh when its connection is configured. Tdarr must be configured separately
to process the imported library; this switch does not start or contact Tdarr.

The default is `false`, preserving normal script processing. Existing config
files are preserved during updates, so add the setting to your existing file.
`video2.bash` uses the shared config when present and keeps its standalone defaults
when that file is absent. The Tdarr switch overrides SMA and language settings.
