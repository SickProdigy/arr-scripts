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


## Failed candidates and retries

A failed copy is identified by the album, peer, and remote folder, rather than
banning the entire peer or giving up on the album. The worker records a local
failure reason and excludes that candidate for `failedDownloadRetryDays`
(default 7 days). Cached alternatives from the same search are tried in quality
order without another search. If those alternatives are exhausted, the album
waits for that failure cooldown before searching again. An initial search with
no suitable candidate waits `retryNotFound` (default 90 days). Timers apply to
albums, so other albums by the same artist can still be searched.

Nicotine API plugin **0.1.2+** is required for coordinated cleanup. It clears
only terminal transfer rows belonging to the recorded batch and never deletes
files itself. Active/queued transfers or ownership conflicts pause recovery;
no alternative batch is started while cleanup remains unresolved. Upgrade the
plugin and disable/re-enable it before updating these scripts.

QueueCleaner skips Soulseek-owned handoffs. The worker records completed import
warnings, clears the client batch, removes the matching Lidarr queue entry and
owned files, then selects the next candidate. Successful handoffs also clear
Nicotine's completed rows before removing original batch files. Unsupported
client cleanup (including the current slskd adapter) retains the job/files for
review instead of guessing at removal. Other download clients retain their
existing native queue-cleaner behavior; this does not introduce a new retry
engine for qBittorrent or NZB clients.

Previously discarded jobs remain discarded; this update does not invent lost
peer/folder identities or automatically retry manually removed jobs.
