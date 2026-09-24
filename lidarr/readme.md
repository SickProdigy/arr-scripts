# README

## Requirements

Container: <https://docs.linuxserver.io/images/docker-lidarr>  

## Installation/setup

1. Add 2 volumes to your container
   `/custom-services.d` and `/custom-cont-init.d` (do not map to the same local folder...)
   Docker Run Example:<br>
   `-v /path/to/preferred/local/folder-01:/custom-services.d`<br>
   `-v /path/to/preferred/local/folder-02:/custom-cont-init.d`
2. Download [scripts_init.bash](https://gitea.rcs1.top/sickprodigy/arr-scripts/src/branch/main/lidarr/scripts_init.bash) ([raw file](https://gitea.rcs1.top/sickprodigy/arr-scripts/raw/branch/main/lidarr/scripts_init.bash)) and place it into the following folder:
   `/custom-cont-init.d`
3. Start your container and wait for the application to load
4. Optional: Customize the configuration by modifying the following file `/config/extended.conf`
5. Restart the container
  
## Updating

Updating is a bit more cumbersome. To update, do the following:

1. Download/update your local `/config/extended.conf` file with the latest options from: [extended.conf](https://gitea.rcs1.top/sickprodigy/arr-scripts/src/branch/main/lidarr/extended.conf)
2. Restart the container, wait for it to fully load the application.
3. Restart the container again, for the new scripts to activate.

This configuration does its best to update everything automatically, but with how the core system is designed, the new scripts will not take affect until a second restart is completed because the container copies/uses the previous versions of the script for execution on the first restart.

## Uninstallation/Removal  

1. Remove the 2 added volumes and delete the contents<br>
   `/custom-services.d` and `/custom-cont-init.d`
1. Delete the `/config/extended.conf` file
1. Delete the `/config/extended` folder and it's contents
1. Remove any Arr app customizations manually.

## Support
[Issues](https://gitea.rcs1.top/sickprodigy/arr-scripts/issues)

## Troubleshooting

### Prowlarr indexer TLS failures

An HTTP 500 from a Lidarr indexer URL such as `http://prowlarr:9696/<id>/api` does not necessarily mean Lidarr cannot reach Prowlarr. Check the response body. Messages such as `The SSL connection could not be established` or `The remote certificate was rejected` mean Prowlarr reached the upstream indexer but rejected its certificate.

Open Prowlarr, locate the indexer matching the numeric `<id>` in the proxy URL, and test it there. Correct the indexer URL or certificate problem, or disable that indexer. Do not publish the `apikey` query value when sharing logs.

### Spotify import-list failures

Spotify import-list failures have two common causes:

* A timeout while contacting `spotify.lidarr.audio` indicates that the renewal service or network path did not respond. Retry later and verify DNS, proxy, and firewall access.
* HTTP 400 with `Invalid token` means the stored refresh token is invalid or revoked. Reauthenticate the Spotify import list, or disable it until authentication is restored.

Saved Albums, Followed Artists, and Playlist imports may each repeat the same underlying authentication error. Diagnose the first renewal response rather than treating every import-list error as a separate failure. Keep refresh tokens and renewal URLs containing tokens out of logs and issue reports. An `Invalid token` response alone does not establish that the Spotify account tier is the cause.

### ReplayGain

ReplayGain support is handled by the standalone `ReplayGainTagger.bash` Lidarr custom script, not by Beets. When `enableReplaygainTags="true"`, AutoConfig registers it for Lidarr release-import and upgrade events, so it runs after the album is in its final Lidarr library path. This covers arr-scripts audio downloads, ordinary Lidarr imports, manual imports, and upgrades/replacements whether Beets is enabled or disabled.

ReplayGain writes loudness metadata tags only. It does not normalize, transcode, or modify audio samples. Players that support ReplayGain use those tags during playback. Partial library coverage can cause large volume jumps: tagged modern albums may be attenuated by 8-10 dB while untagged tracks play at full volume, which is especially noticeable during shuffle and CarPlay playback.

Configuration options in `/config/extended.conf`:

* `enableReplaygainTags`: enables post-import ReplayGain tagging.
* `replaygainTargetLoudness`: target loudness in LUFS. The default is `-18`, the ReplayGain 2.0 standard. This may sound quieter than untagged files; use a higher player preamp/amplifier setting if desired rather than silently changing the scanner target.
* `replaygainThreads`: positive integer or `MAX`, controls parallel album jobs for explicit backfill workflows. Normal Lidarr events process one imported album folder.
* `replaygainPreserveMtime`: preserves file modified times while writing metadata tags.
* `replaygainClipMode`: `n` disables clipping protection, `p` protects positive gain values, and `a` protects all gain values.
* `replaygainTruePeak`: enables inter-sample true-peak measurement when set to `true`.
* `replaygainMaxPeak`: maximum playback peak in dB when clipping protection is applied. For example, `-1` provides 1 dB of true-peak headroom.

Use track gain for shuffle, playlists, and mixed playback. Use album gain when listening to full albums so intentional track-to-track dynamics are preserved. The script writes both track and album tags for supported formats. Some clients, including current Amperfy releases, always use track gain even though they import album-gain metadata.

Existing libraries are not backfilled automatically during updates. To inspect current coverage without writing tags:

```bash
/config/extended/ReplayGainTagger.bash --audit --path /music
```

To perform a dry-run backfill audit:

```bash
/config/extended/ReplayGainTagger.bash --backfill --dry-run --path /music
```

To explicitly backfill `/music`, writing ReplayGain metadata tags:

```bash
/config/extended/ReplayGainTagger.bash --backfill --path /music
```

The audit reports complete track+album tag coverage, partial tags, untagged files, unreadable or unsupported files, and albums with mixed tag coverage.


## Features

<table>
  <tr>
    <td><img src="https://github.com/RandomNinjaAtk/docker-lidarr-extended/raw/main/.github/lidarr.png" width="150"></td>
    <td><img src="https://github.com/RandomNinjaAtk/docker-lidarr-extended/raw/main/.github/plus.png" width="75"></td>
    <td><img src="https://github.com/RandomNinjaAtk/docker-lidarr-extended/raw/main/.github/music.png" width="150"></td>
    <td><img src="https://github.com/RandomNinjaAtk/docker-lidarr-extended/raw/main/.github/plus.png" width="75"></td>
    <td><img src="https://github.com/RandomNinjaAtk/docker-lidarr-extended/raw/main/.github/video.png" width="150"></td>
  </tr>
 </table>

* Downloading **Music** using online sources for use in popular applications (Plex/Kodi/Emby/Jellyfin):
  * Completely automated
  * Searches for downloads based on Lidarr's album missing & cutoff list
  * Downloads using a third party download client automatically
  * FLAC (lossless) / MP3 (320/128) / AAC (320/96) Download Quality
  * Can convert Downloaded FLAC files to preferred audio format and bitrate before import into Lidarr
  * Notifies Lidarr to automatically import downloaded files
  * Music is properly tagged and includes coverart before Lidarr Receives them
  * Can pre-match and tag files using Beets
  * Can add ReplayGain 2.0 track and album metadata tags after Lidarr import
  * Can add top artists from online services
  * Can add artists related to your artists in your existing Library
  * Can notify Plex application to scan the individual artist folder after successful import, thus increasing the speed of Plex scanning and reducing overhead
* Downloading **Music Videos** using online sources for use in popular applications (Plex/Kodi/Emby/Jellyfin):
  * Completely automated
  * Searches Lidarr Artists (musicbrainz) video recordings for videos to download
  * Saves videos in MKV format by default
  * Downloads using Highest available quality for both audio and video
  * Saves thumbnail of video locally for Plex/Kodi/Jellyfin/Emby usage
  * Embed subtitles if available matching desired language
  * Automatically Add Featured Music Video Artists to Lidarr
  * Writes metadata into Kodi/Jellyfin/Emby compliant NFO file
    * Tagged Data includes
      * Title (musicbrainz)
      * Year (upload year/release year)
      * Artist (Lidarr)
      * Thumbnail Image (service thumbnail image)
      * Artist Genere Tags (Lidarr)
  * Embeds metadata into Music Video file
    * Tagged Data includes
      * Title (musicbrainz)
      * Year (upload year/release year)
      * Artist (Lidarr)
      * Thumbnail Image (service thumbnail image)
      * Artist Genere Tags (Lidarr)
* Queue Cleaner Script
  * Automatically removes downloads that have a "warning" or "failed" status that will not auto-import into Lidarr, which enables Lidarr to automatically re-search for the album
* Unmapped Folder Cleaner Script
  * Automatically deletes folders that are not mapped in Lidarr
* ARLChecker Script
  * Checks Deezer ARL set in extended.conf at set interval for validity
  * Reports ARL status in text file
  * Optional Telegram bot with ability to set token from the chat
  * Optional Pushover and ntfy notification upon ARL token expiration

For source and updates, visit the [repository](https://gitea.rcs1.top/sickprodigy/arr-scripts).

### Audio & Video (Plex Example)

![plex](https://github.com/RandomNinjaAtk/docker-lidarr-extended/raw/main/.github/plex.png)

### Video Example (Kodi)

![kodi](https://github.com/RandomNinjaAtk/docker-lidarr-extended/raw/main/.github/kodi-music-videos.png)

## Credits

* [LinuxServer.io Team](https://github.com/linuxserver/docker-lidarr)
* [Lidarr](https://lidarr.audio/)
* [Beets](https://beets.io/)
* [Deemix download client](https://deemix.app/)
* [Tidal-Media-Downloader client](https://github.com/yaronzz/Tidal-Media-Downloader)
* [rsgain](https://github.com/complexlogic/rsgain)
* [Algorithm Implementation/Strings/Levenshtein distance](https://en.wikibooks.org/wiki/Algorithm_Implementation/Strings/Levenshtein_distance)
* [ffmpeg](https://ffmpeg.org/)
* [yt-dlp](https://github.com/yt-dlp/yt-dlp)
* [SMA Conversion/Tagging Automation Script](https://github.com/mdhiggins/sickbeard_mp4_automator)
* [Freyr](https://github.com/miraclx/freyr-js)
