# SABnzbd post-processing

These scripts process completed SABnzbd downloads before Sonarr, Radarr, or
Lidarr imports them. Video processing can run here or be left to Tdarr using
one setting in `extended.conf`.

## Requirements

Container: [https://docs.linuxserver.io/images/docker-sabnzbd](https://docs.linuxserver.io/images/docker-sabnzbd)  

## Installation

Open a linked script and save its **raw contents**, rather than the HTML page,
when downloading it. Relative links use whichever checkout or mirror you are
viewing; the [public GitHub mirror](https://github.com/SickProdigy/arr-scripts)
is available for public downloads.

1. Add volume to your container
  `/custom-cont-init.d`
  Docker Run Example:
  `-v /path/to/preferred/local/directory:/custom-cont-init.d`
1. Download the [scripts_init.bash](scripts_init.bash) and place it into the following folder: `/custom-cont-init.d`
1. Start your container and wait for the application to load
1. Customize `/config/extended.conf` for your processing options and Arr connections.
1. Add the `/config/scripts` folder to the "Scripts Folder" folder setting in SABnzbd
1. Add `video.bash` or `audio.bash` script to the appropriate SABnzbd category

Installing the scripts does not assign them to categories automatically. Place
the initialization script in `/custom-cont-init.d`, rather than `/custom-services.d`.

## Choose a script

| Script | Purpose | Installation |
| --- | --- | --- |
| `video.bash` | Video language checks and optional Sickbeard MP4 Automator (SMA) conversion/tagging | Automatic |
| `video2.bash` | Alternative video processor: MKV remuxing, audio/subtitle filtering, and MKV metadata edits | Manual |
| `audio.bash` | Audio conversion, Beets tagging, and ReplayGain according to your settings | Automatic |
| `audiobook.bash` | Audiobook post-processing | Automatic |

Choose one video script for a category. To use the alternative, save
[video2.bash](video2.bash)
as `/config/scripts/video2.bash`, then select it for that category. The standard
installer does not install or update this alternative automatically.

`video.bash` uses the Arr connection settings in `extended.conf` and recognizes
`radarr`, `radarr4k`, `sonarr`, `sonarr4k`, and `sonarranime` categories.
`video2.bash` selects its Arr connection from a download path containing
`radarr`, `sonarr`, or `sonarr-anime`. It reads the shared configuration when
present, including `radarrArrUrl`/`radarrArrApiKey`,
`sonarrArrUrl`/`sonarrArrApiKey`, and
`sonarranimeArrUrl`/`sonarranimeArrApiKey`. Without that file, it uses the settings
at the top of the script.

## Updating

Ensure your initialization script points to **SickProdigy/arr-scripts**. Older
copies pointing to RandomNinjaAtk install upstream scripts instead. Replace the
initialization script with the download linked above if necessary.

1. Compare your local `/config/extended.conf` with the [configuration template](extended.conf)
   and add new options. Preserve your existing settings and API keys.
1. Restart the container, wait for it to fully load the application.
1. Restart the container again, for the new scripts to activate.
1. If you use `video2.bash`, update that script manually from the link above.

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

| Processing step | Tdarr toggle enabled |
| --- | --- |
| Check that a supported video file exists | Kept |
| Reject files based on audio/subtitle languages | Skipped |
| Remove or filter audio/subtitle tracks | Skipped |
| Convert video or remux into MKV | Skipped |
| Edit video metadata or track statistics | Skipped |
| Delete artwork, sidecars, or other download files | Skipped |
| Complete SABnzbd post-processing for Arr import | Kept |

Supported video extensions for this presence check are `.mkv`, `.mp4`, `.m4v`,
`.avi`, and `.wmv`. A download with no matching video file fails the check;
its files are preserved in Tdarr mode. This check does not validate video contents.
Audio and audiobook scripts are unaffected.

Set `letTdarrHandleVideo="false"` to restore the usual video processing. Changing
this setting takes effect on the next post-processing invocation once the
updated video script is installed; the setting itself needs no restart.
