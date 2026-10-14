# Contributor notes

## Lidarr helper releases

Lidarr installs both independent packages alongside tools such as Beets:

| Package | Repository | Release pin | Responsibility |
| --- | --- | --- | --- |
| sonicratings 1.4.0 | SonicRatings | `v1.4.0` | Generate Subsonic ratings, queue imports, and retain rating provenance |
| musicbrainz-helper 0.4.0 | musicbrainz-helper | `v0.4.0` | MusicBrainz rating/genre contributions and submission history |

`InstallHelpers.bash` installs pinned Git releases through `uv pip` and verifies versions and commands. Lidarr setup installs their Requests, Mutagen, Colorama, python-dotenv, and tqdm dependencies. Neither package imports or installs the other; arr-scripts coordinates their commands and passes optional provenance paths through environments.

`MusicBrainzGenres.bash` invokes `musicbrainz-genres`. `Ratings.bash` invokes `sonicratings` and `musicbrainz-ratings` according to their enable settings. Services, callbacks, and credentials belong in arr-scripts; Python behavior stays in the respective packages. Both packages use the same code for manual and installed commands.

## Release and deploy

Publish **SonicRatings `v1.4.0`** and **musicbrainz-helper `v0.4.0`** before deploying this installer. Missing tags or version mismatches fail setup instead of selecting a moving branch. Publish arr-scripts to its Gitea origin, then use the normal container restart/update workflow. Setup version `1.4.35` refreshes dependencies in existing installations. Existing configuration is preserved; add the desired new settings from [ratings setup](lidarr/readme.md#ratings-automation).

Sources default to public GitHub repositories under `SickProdigy`, regardless of where arr-scripts itself was downloaded. `ARR_HELPER_REPOSITORY_BASE` overrides that base. For package validation, `ARR_SONICRATINGS_SOURCE` and `ARR_MUSICBRAINZ_HELPER_SOURCE` accept local package directories or reviewed wheels accessible inside the container. Version checks still apply. The obsolete `ARR_SONICRATINGS_ENGINE_SOURCE` override is unused.

## Ratings worker behavior

Callbacks snapshot local track identities after successful tagging. With Beets enabled, imports enqueue after Beets finishes; retags queue independently. Callbacks do not query providers or write ratings.

The service checks queues every minute. SonicRatings waits until the matching tracks appear after the Subsonic server scan. It verifies artist, album, title/version, recording IDs when available, and paths; ambiguous or changed matches stay queued. It fills unrated songs and rechecks the personal rating before writing. Provider misses and failures retry with increasing delays up to one hour. Existing personal ratings are preserved.

Generated ratings retain their source and recording identity. The MusicBrainz helper reads that provenance so MusicBrainz-derived stars are not submitted back as fresh votes. Last.fm, Spotify, TIDAL, and Discogs-derived votes can be contributed. Later personal changes remain eligible. An uncertain write retains its source so a successful remote write is recognizable on retry.

SonicRatings stores jobs, update locks, logs, and provenance under `/config/extended/sonicratings/<server-account>/`. MusicBrainz observations and account ledgers remain under `/config/extended/ratings-helper/`. Back up both with the container configuration. Keep the same Subsonic URL and username to retain their scope. Old helper queues and submission history are preserved; old pending Subsonic-write jobs from the previous helper are not resumed.
