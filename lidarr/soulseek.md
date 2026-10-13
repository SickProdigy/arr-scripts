# Soulseek for Lidarr

Soulseek fills monitored missing albums through slskd or the Nicotine+ API plugin.
Install/update the Lidarr scripts using the [Lidarr README](readme.md), then
follow [slskd setup](../universal/soulseek.md#example-slskd-service) or the
[Nicotine+ API plugin instructions](https://github.com/SickProdigy/nicotineplus-plugins).
Nicotine requires its `api` plugin, a key of at least 32 random characters,
listen address `0.0.0.0` for separate containers, and port `12339`. Save settings
and disable/re-enable the plugin. Both clients must be running and connected.

## Standard download layout

Keep these Arr-side paths unless your mounts require something different:

```bash
downloadPath="/downloads/lidarr/downloads"
importPath="/downloads/lidarr/import"
soulseekDownloadPath="/downloads/soulseek"
```

Mount your general download directory at `/downloads` for Deezer/TIDAL. For
Nicotine automation, mount a dedicated host directory into both containers:

```yaml
# Nicotine container volumes
- /your/downloads/nicotineplus/arr-scripts:/data/arr-scripts
# Lidarr container volumes
- /your/downloads/nicotineplus/arr-scripts:/downloads/soulseek
```

Set the plugin's **Completed download root** to `/data/arr-scripts`, an absolute
path inside Nicotine. Your personal completed/incomplete download mounts remain
separate. For slskd, mount its configured completed root at `/downloads/soulseek`
inside Lidarr instead. Both containers need read/write access to the same host
directory and a shared Docker network. Recreate containers after mount changes.

## extended.conf

For Nicotine+:

```bash
enableSoulseek="true"
soulseekClient="nicotine"
soulseekUrl="http://nicotineplus:12339"
soulseekApiKey="YOUR_PLUGIN_API_KEY"
soulseekDownloadPath="/downloads/soulseek"
dlClientSource="all"
```

Use your Nicotine service name or network alias in the URL and the same key as
the plugin. No host port publication is needed on a shared Docker network.
Keep the API reachable only on your private network.

For slskd **0.26.0+**, change these three settings:

```bash
soulseekClient="slskd"
soulseekUrl="http://slskd:5030"
soulseekApiKey="YOUR_READ_WRITE_SLSKD_API_KEY"
```

`all` selects Deezer/TIDAL with Soulseek fallback; their usual authentication
still applies. Use `soulseek` for Soulseek only without a fallback delay, or
`deezer,soulseek` / `tidal,soulseek` for one streaming source with fallback.
With multiple sources, confirmed streaming misses are eligible immediately;
unavailable streaming clients can fall back after the default one-hour delay.

## Imports and cleanup

Nicotine manages its own transfers. Completed albums are staged outside the
watch folder, then published atomically into `importPath`, using the existing
**Arr-Extended / Usenet Blackhole** client. An enabled blackhole client must
watch that same path, including for Soulseek-only setups. The worker requests a
native download refresh; it does not submit a competing direct import.

Soulseek albums appear in **Activity -> Queue** when ready for Lidarr, like
Tidal/Deezer. Successfully imported albums disappear. Rejected albums remain
available for manual import or removal with **delete files** selected. The
single Soulseek slot remains occupied until import or coordinated rejection
recovery finishes, preventing a stack of additional rejected downloads. Tidal and Deezer continue separately.
The blackhole client does not report Nicotine's live transfer/start status.

Folders use `Artist-Album (Year) [Soulseek]` unless Lidarr recognizes an existing
release group, which is preserved instead. Library filenames show that group
when it is included in Lidarr's renaming format. There is only one completed
copy after a successful handoff; temporary staging copies are removed from the
Soulseek batch. The worker verifies final library files before cleaning any
remaining handed-off files. Personal downloads and unknown files are preserved.

Older direct-import failures remain in `review`. To hand one to the new queue,
run `ARR_SOULSEEK_APP=lidarr bash /config/extended/Soulseek.bash --retry ITEM_ID`.
The existing `--discard ITEM_ID` command still handles those older reviewed
batches. New handed-off albums are removed through Lidarr's Activity queue.

New Nicotine batches go directly under the plugin's completed-download root:
`/data/arr-scripts/<batch UUID>`, seen by Lidarr as
`/downloads/soulseek/<batch UUID>`. There are no additional `arr-scripts` or
`lidarr` subfolders. Previously queued jobs retain their original location until
finished or discarded; deleting a directory does not cancel an active transfer.

## Audio format and quality

Soulseek uses Lidarr's existing `audioFormat`, `audioBitrate`, and
`requireQuality` settings. Searches remain `Artist Album`; returned files are
ranked by format and available audio attributes, rather than searching each
extension separately. Complete albums from one peer are required.

With `audioFormat="native"`, `master` prefers high-resolution lossless and
`lossless` prefers lossless. When `requireQuality="false"`, lossy formats such
as MP3 are accepted as fallback. `high` and `low` prefer lossy audio near
320 and 128 kbps respectively. M4A can contain AAC or ALAC; the extension alone
does not establish lossless quality.

For `mp3`, `aac`, `opus`, or `alac`, use a numeric `audioBitrate` as with the
streaming downloader. Lossless sources are converted in private staging before
handoff. Existing lossy fallback files keep their original encoding; they are
never upconverted to lossless or re-encoded to claim a higher bitrate. Opus
conversion preserves text metadata but does not copy attached cover art.

Strict quality excludes unsuitable or unverifiable search results and probes
downloaded audio with ffprobe before publication. Conversion uses ffmpeg and
verifies the output codec; an encoder's target bitrate is not a guarantee of
an exact measured bitrate. Quality/conversion failures are retained for review.
Peer attributes remain hints, not proof of the original recording's fidelity.
Settings are saved with each new search so active batches retain their policy.
These audio settings do not affect Sonarr or Radarr.


## Failed candidates and background downloads

Nicotine API plugin **0.1.3+** is required for the offline/background lifecycle.
Upgrade the plugin files and disable/re-enable it before updating these scripts.

Search results are cached and ranked by quality. Failed copies are excluded by
album, peer, and remote folder for `soulseekFailedRetryDays` (default 30 days).
This does not ban the peer's other releases. Cached alternatives are tried
without another search. Exhausting failed candidates uses
`failedDownloadRetryDays` (default 7 days) before a new search; an initial search
with no suitable match uses `retryNotFound` (default 90 days).

After `soulseekOfflineDelay` (default 900 seconds / 15 minutes) continuously
offline, the worker leaves the original batch queued in Nicotine and frees the
foreground slot for another candidate or album. Nicotine resumes automatically
when the peer returns. Background batches are monitored on each worker pass.
An online peer resets the offline clock; ordinary slow downloads and online
upload queues are not treated as offline.

After `soulseekOfflineGrace` (default 86400 seconds / 24 hours) continuously
offline, the worker cancels and clears the owned batch, removes its owned
leftovers, and applies the candidate exclusion. A completed background copy can
become the import candidate before expiry. Completed copies still need native
Lidarr acceptance; completion alone never cancels alternatives. Once library
files are verified, redundant background copies are cancelled and cleaned.

`soulseekMaxBatches` defaults to **3 concurrent searches/download batches per
app** and accepts values from 1 through 10. It includes foreground searches,
active downloads, retained review batches, and offline background copies. At
the cap, additional candidates wait. This limits album batches, not individual
track files, and is not an attempts-per-day limit. State, candidate identity,
and offline timestamps survive restarts.

QueueCleaner skips Soulseek-owned handoffs. Explicit failures cancel only the
recorded batch before clearing its client rows and deleting owned files. The
Nicotine plugin checks all transfer destinations and sizes before cancellation;
foreign GUI transfers and uncertain ownership halt recovery. Its optional
incomplete-file cleanup removes only validated partials belonging to the
cancelled batch. Personal downloads and unknown files remain untouched.

The current slskd adapter does not expose the verified offline/cancellation
contract, so unsupported recovery retains its job/files for review. Other
clients retain their native queue-cleaner behavior. Previously discarded jobs
remain discarded; the worker does not invent lost peer/folder identities.
