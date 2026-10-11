# Integrated ratings

Works with any Subsonic/Navidrome server that supports the required Subsonic APIs:
JSON responses, salted-token authentication, album/song browsing, personal
`userRating` reads and `setRating` writes. MusicBrainz submission also requires
valid recording IDs from the server or matched Lidarr import snapshots. Other
server implementations have not yet been tested live.

The ratings helper runs inside the existing Lidarr container. Setup installs
versioned musicbrainz-helper and SonicRatings engine packages, using the same model as
Beets. `Ratings.bash` calls the installed `musicbrainz-ratings` command. That
package maintains Subsonic synchronization and MusicBrainz contribution history;
SonicRatings maintains provider generation in its import-safe engine. Neither standalone
application nor a GUI service is launched. See [dependency installation](helper-dependencies.md).

## Configuration

Add the following to `/config/extended.conf`:

```bash
enableRatings="true"
subsonicUrl="http://navidrome:4533"
subsonicUsername="your_subsonic_user"
subsonicPassword="your_subsonic_password"
```

Use a URL reachable from the Lidarr container, including any Subsonic base path.
The username selects whose personal ratings are read and written. Existing
configuration files are preserved by setup, so new settings must be added there.
The preferred names are `subsonicUrl`, `subsonicUsername` and `subsonicPassword`.
Older `navidromeUrl`, `navidromeUsername` and `navidromePassword` settings remain
accepted as fallbacks. New nonempty settings take precedence. The package likewise
accepts legacy `NAVIDROME_*` environment variables if `SUBSONIC_*` are absent.
Using the same server URL and username preserves the existing state directory
and submission history when renaming settings.
The normal installer includes `Ratings.bash`, its background service, and the
installed helper packages. Refresh the installed scripts and restart the Lidarr container
to install the service; AutoConfig registers import, upgrade and retag callbacks
when `configureCustomScripts="true"`.

Two optional settings have defaults:

```bash
lastfmApiKey=""            # Empty: use MusicBrainz; supplied: use Last.fm scoring
ratingsScriptInterval=15m   # Periodic library rating change checks
```

No provider-selection setting is required. Without a Last.fm API key, the helper
looks up the exact MusicBrainz recording's community rating and rounds it to
Subsonic's integer stars. With a key, it uses SonicRatings's artist-rank, listener-reach
and repeat-listening formula; these are generated popularity-based ratings.
It preserves version qualifiers and rejects conflicting returned recording IDs
or artist/title matches. An API failure retries later; a successful lookup with
no usable rating leaves the song unrated. There is no fallback from a failing
Last.fm lookup to MusicBrainz. An explicit library pass can recheck songs that
previously had no usable provider rating.

To contribute ratings, use the same settings as the genre helper:

```bash
enableMusicBrainzHelper="true"
musicBrainzUsername="your_musicbrainz_user"
musicBrainzToken="your_musicbrainz_application_token"
```

No second MusicBrainz account or credential file is needed. Credentials stay in
`extended.conf` and worker environment variables; they are not written to job
files, reports, command arguments or logs. Subsonic authentication uses salted
Subsonic tokens, and MusicBrainz uses HTTP Digest with the application token.
Keep `extended.conf` private.

## Automatic workflow

1. A successful Beets import queues the album's embedded MusicBrainz recording
   IDs. With Beets disabled, the Lidarr import notification queues directly.
2. The worker checks import jobs every minute. Tracks not yet present in Subsonic
   remain queued. Matching requires an unambiguous file path or an exact recording
   ID together with artist, title and album; conflicting IDs are rejected.
3. Unrated imported songs get generated stars. Already-rated songs are preserved,
   and the worker checks again immediately before writing.
4. Last.fm-generated stars submit immediately when MusicBrainz contributions are
   enabled and credentials are present. MusicBrainz-derived stars stay local.
5. Every `ratingsScriptInterval`, the worker scans the Subsonic library and sends
   changed personal ratings to the exact MusicBrainz recording. Clearing a rating
   withdraws a vote previously submitted by this helper. It does not refill a song
   you cleared, infer deletions from missing songs, or withdraw unknown votes.

Generated Last.fm stars count as your personal MusicBrainz votes and affect the
community average. Source and generated values are recorded so MusicBrainz-derived
stars never automatically feed back into MusicBrainz. A later change in Subsonic
is treated as your selected rating, including changes to a MusicBrainz-derived
value. Changes made by another automation cannot be distinguished from changes
you make in a player; avoid having multiple tools update the same user's ratings.
Setting a rating to the same value is not detectable through polling. Changes
made and reversed between polls are also not observable.

On its first scan, the helper baselines existing ratings of unknown origin and
does not submit them unchanged: they may have been generated from MusicBrainz by
an older tool. Subsequent changes become eligible. Songs without a valid recording
ID cannot contribute. If multiple copies of a recording have different ratings,
that recording is skipped until the values agree.

Failed Subsonic writes preserve generation intent before retrying, including
when the server accepted a write but its response was lost. Failed MusicBrainz
submissions retry without rewriting Subsonic; successful submissions update an
account-specific ledger. Turning MusicBrainz contributions off leaves local
rating generation and change tracking running. Turning `enableRatings` off stops
both rating generation and rating synchronization.

## Existing library and state

Rate the existing library's unrated songs explicitly:

```bash
bash /config/extended/Ratings.bash --library
```

This writes stars to Subsonic and submits eligible generated ratings when
enabled. Normal periodic scans observe the whole library but only automatically
generate stars for import jobs and unfinished retries. External retagging can
queue an existing Lidarr album explicitly:

```bash
bash /config/extended/Ratings.bash --after-beets 123
```

State lives in `/config/extended/ratings-helper/<account-hash>/`, selected from
the Subsonic URL and username. `state.json` preserves observed ratings, provenance
and pending writes; `jobs/` holds pending import work; `submitted-*.json` tracks
accepted votes separately for each MusicBrainz account. Back up this directory
with the container configuration. Changing the Subsonic URL selects new state;
keep it consistent. Deleting state loses provenance and withdrawal history.

The service reports counts and failures in container logs. Requests share a worker
lock with genre submissions across configured MusicBrainz accounts. API calls are
paced, and submissions use the current Subsonic personal `userRating`, never its
community/average rating. The helper writes Subsonic ratings, not embedded music
file tags; Beets continues to own the existing tagging workflow.

References: [Subsonic rating endpoint](https://opensubsonic.netlify.app/docs/endpoints/setrating/),
[MusicBrainz rating system](https://musicbrainz.org/doc/Rating_System),
[Last.fm track information](https://www.last.fm/api/show/track.getInfo),
[Last.fm artist top tracks](https://www.last.fm/api/show/artist.getTopTracks).
SonicRatings attribution is included with the installed engine package.
