# Helper dependencies

Lidarr installs these packages alongside tools such as Beets:

| Package | Repository | Release pin | Responsibility |
| --- | --- | --- | --- |
| musicbrainz-helper 0.3.0 | musicbrainz-helper | `v0.3.0` | Genre contributions, Subsonic rating observation, MusicBrainz submissions and retry history |
| sonicratings-engine 0.1.0 | sonicratings (local folder: SonicRatings) | `engine-v0.1.0` | Provider lookups and rating generation, usable by automation or a future GUI |

`InstallHelpers.bash` installs both directly from their Git repositories at those
tags with `uv pip`, then verifies the installed versions and entry points.
It does not fetch these project names from PyPI. Requests and Mutagen are already
installed by Lidarr setup. Package dependencies are installed on script refresh
as well as first setup. Source files stay in their respective projects.

`MusicBrainzGenres.bash` invokes `musicbrainz-genres`; `Ratings.bash` invokes
`musicbrainz-ratings`. Services, event callbacks, enable flags and credentials
remain in arr-scripts. Jobs, cache, provenance and submission ledgers keep their
existing paths under `/config/extended/`. Old loose Python files from a previous
installation are no longer invoked, and setup does not delete user files or state.

## Release and deploy

The new package code and release tags must be committed and published to their
repositories before deploying the arr-scripts setup change. These tags are release
requirements, not a claim that they have already been published. Release SonicRatings's
engine first, then musicbrainz-helper, then the arr-scripts integration. Missing
tags or incorrect package versions cause setup to fail instead of selecting a
moving branch or silently installing another implementation.

Normal configuration needs no source URLs. Dependencies default to the public
GitHub repositories `SickProdigy/musicbrainz-helper` and `SickProdigy/SonicRatings`,
regardless of where arr-scripts itself was downloaded. Setup also defaults to the
public GitHub arr-scripts mirror. For another layout, set the setup environment
variable `ARR_HELPER_REPOSITORY_BASE` to the forge/owner base URL. This is an
installer override, not an `extended.conf` setting.

For local development, `ARR_SONICRATINGS_ENGINE_SOURCE` and
`ARR_MUSICBRAINZ_HELPER_SOURCE` may point to local package directories or reviewed
wheel files accessible inside the container. The same version checks apply. This
supports verifying unreleased changes without publishing or using moving branches.

The SonicRatings engine has no GUI dependency and does not start the standalone SonicRatings
sync application. A future GUI can call its Python API, while arr-scripts continues
using the package through musicbrainz-helper. Existing standalone SonicRatings remains
available separately.
