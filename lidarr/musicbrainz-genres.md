# MusicBrainz helper

The helper is built into the normal Lidarr scripts installation. It contributes
existing Tidal/Deezer or Beets genre tags to MusicBrainz after imports, upgrades
and Lidarr retags. It also supports a whole-library genre export and contribution
pass. Beets and Navidrome are not required.

## Configuration

Add these settings to your existing `/config/extended.conf`:

```bash
enableMusicBrainzHelper="true"
musicBrainzUsername="your_musicbrainz_username"
musicBrainzToken="your_musicbrainz_application_token"
```

Create an application token under MusicBrainz Account > Applications. Use that
token, not your website login password. The helper authenticates with your username
and token using HTTP Digest over HTTPS.

This is the complete genre configuration. Keep `extended.conf` private, as with its
other credentials. Setup includes the helper and its worker with the other
scripts; there is no separate container, mount, credentials file, profile setting
or helper download to configure. Update the installed scripts and restart Lidarr
when deploying this change. Setup preserves an existing `extended.conf`, so add
these three settings to existing installations.

With `configureCustomScripts=true`, AutoConfig registers the import, upgrade and
track-retag notification. When Beets is enabled, imports queue after Beets finishes
rather than depending on notification order. The worker submits contributions
under the configured MusicBrainz account and polls every minute. It requires both
credentials; blank credentials never cause an anonymous submission. Set
`enableMusicBrainzHelper="false"` to stop queuing and processing.

The account's username automatically selects a local profile beneath
`/config/extended/musicbrainz-helper/profiles/`. Profile directory names include a
short account hash to distinguish usernames with similar spellings. Jobs, local
exports, reports and accepted-vote history are kept there. Credentials are passed
through the worker's environment, not its command-line arguments or reports.

## Library passes and inspection

Queue the genres of every present Lidarr album and save a local genre export:

```bash
bash /config/extended/MusicBrainzGenres.bash --library
```

Repeat after retagging with Beets, Picard or another external tagger. Lidarr's own
retag events are handled automatically; external taggers must call this helper
or run a library pass. The library pass queues contributions for the enabled
worker, and saves timestamped JSON exports in the account's `library-snapshots/`.
Exports retain raw genre values, paths and selected matching identifiers,
including custom genres that cannot be submitted. Unreadable albums are reported
while other albums continue. These are genre metadata exports, not audio or full
metadata backups; restoration is not implemented.

Queue one existing Lidarr album explicitly:

```bash
bash /config/extended/MusicBrainzGenres.bash --after-beets 123
```

Inspect the generated `genres.csv`, `musicbrainz-tags.xml`, `summary.json` and
`failed-submissions.csv` in the account's `reports/` directory. For an explicit
manual preview of queued jobs, use:

```bash
bash /config/extended/MusicBrainzGenres.bash --work --preview
```

This command does not submit. The enabled background worker remains in automatic
contribution mode; a manual preview is not a pause or an approval gate.

## Contribution behavior

Album genres target the matching release group and its known release editions.
Singles, EPs and albums share this path. Exact embedded recording IDs receive
song genres when verified within those releases; title-only matching does not
spread a song's genres to remixes or live versions. Album artist suggestions are
also included when Lidarr supplies its MusicBrainz ID. Suggestions are limited to
seven recognized genres per entity.

Test events queue nothing. Durable jobs retry failures separately from imports,
and accepted votes are tracked per account to prevent repeated contributions.
Changed tags queue new suggestions; removing a local genre does not withdraw an
earlier MusicBrainz vote. Genre downvotes and deletions are not included.
The integrated [ratings helper](readme.md#ratings-automation) additionally submits eligible
Navidrome ratings when this same MusicBrainz enable flag and credentials are set.

Final embedded tags represent the user's selected genres. Beets can combine
existing provider, Last.fm and MusicBrainz genres; processing-path labels do not
imply independent evidence from each provider. Missing genre tags generate no
suggestions. MusicBrainz retains account-specific genre votes, while the local
exports preserve the original choices. It is not a complete library backup.

Setup installs the versioned `musicbrainz-helper` package, just as it installs
Beets. The Bash wrapper calls its installed `musicbrainz-genres` command. Python
contribution logic is maintained in that project's `musicbrainz_helper/` folder,
not copied into arr-scripts. See [helper dependency installation](../CONTRIBUTING.md#lidarr-helper-releases)
for package pins and release/deployment requirements.
