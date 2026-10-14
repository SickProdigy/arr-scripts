# arr-scripts

Public GitHub mirror: [SickProdigy/arr-scripts](https://github.com/SickProdigy/arr-scripts).

SickProdigy's arr-scripts extends LinuxServer.io Arr containers with automated
downloads, processing, imports, and library maintenance. It builds on
RandomNinjaAtk's scripts, previously packaged with the Extended containers,
and continues their development here.

Choose your application below for installation. Optional features are configured
in its `extended.conf`.

## Applications

| Application | Features and setup |
| --- | --- |
| [Lidarr](lidarr/readme.md) | Deezer/TIDAL downloads, Soulseek alternative, music tagging, ReplayGain, lyrics, MusicBrainz contributions, and Subsonic/Navidrome ratings |
| [Sonarr](sonarr/readme.md) | TV automation, queue maintenance, extras, YouTube series downloads, and Soulseek alternative |
| [Radarr](radarr/readme.md) | Movie automation, queue maintenance, extras, and Soulseek alternative |
| [Readarr](readarr/readme.md) | Existing Readarr scripts and setup |
| [SABnzbd](sabnzbd/readme.md) | Existing SABnzbd scripts and setup |

Start with your application's README. Each folder contains its scripts,
configuration, and installation instructions. Shared workers and common guides
are documented in the [shared scripts README](universal/readme.md).

## Companion projects

- [nicotineplus-plugins](https://github.com/SickProdigy/nicotineplus-plugins):
  the Nicotine+ HTTP API for automated Soulseek searches and downloads.
- [musicbrainz-helper](https://github.com/SickProdigy/musicbrainz-helper):
  optional MusicBrainz contributions and synchronization integrated with Lidarr.
- [SonicRatings](https://github.com/SickProdigy/SonicRatings): ratings automation
  integrated with Subsonic/Navidrome and the Lidarr import workflow.

## Installation and updates

The scripts are designed for [LinuxServer.io](https://www.linuxserver.io/)
containers. Follow your application's instructions for custom initialization
and service mounts, `extended.conf`, and updates. Existing custom configuration
is preserved; review new options when updating.

Downloads and final library files must be accessible inside the participating
containers. Use the documented default paths and shared mounts. Keep credentials
in local configuration and remove them from logs before sharing.

## Support and credits

Report problems in this repository's issues with the application, script version,
relevant configuration, and logs with credentials removed. These integrations
are maintained here and are not supported by the Arr applications' support teams.

Thanks to [RandomNinjaAtk](https://github.com/RandomNinjaAtk/arr-scripts) for the
original scripts, and to the Arr, LinuxServer.io, MusicBrainz, Soulseek, and other
open-source projects used by this bundle. Application READMEs include additional
tool credits.
