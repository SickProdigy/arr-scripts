# Universal scripts

Shared scripts used by the application folders. Install them through the
[Lidarr](../lidarr/readme.md), [Sonarr](../sonarr/readme.md),
[Radarr](../radarr/readme.md), or [Readarr](../readarr/readme.md) setup instructions;
this folder has no separate installer or container. Settings go in the application's
`/config/extended.conf`. Restart that container after configuring.

## What's here

| File | Purpose |
| --- | --- |
| [functions.bash](functions.bash) | Shared logging, configuration checks, and Arr connection helpers used by the other scripts. |
| [dashboard.py](dashboard.py) | Optional web dashboard for approved tasks, job status, and logs. |
| [services/QueueCleaner](services/QueueCleaner) | Removes stuck or failed queue items so the Arr app can retry. |
| [services/Recyclarr](services/Recyclarr) | Updates Sonarr/Radarr quality profiles, quality limits, and custom-format scores. |
| [Soulseek.bash](Soulseek.bash) | Starts the shared Soulseek worker using your app's configuration. |
| [Soulseek.service.bash](Soulseek.service.bash) | Runs that worker automatically; the app installer selects Lidarr, Sonarr, or Radarr. |
| [python/Soulseek.py](python/Soulseek.py) | Handles searches, downloads, and imports through slskd or the Nicotine+ API plugin. |
| [python/SoulseekAudio.py](python/SoulseekAudio.py) | Applies Lidarr audio-quality and conversion settings to Soulseek downloads. |
| [tests/](tests/) | Tests for Soulseek matching, audio processing, and the Bash wrapper. |

`functions.bash` is loaded by scripts automatically; users do not run it separately.
The installers place background services in `/custom-services.d/` and supporting
scripts in `/config/extended/`.

## Optional web dashboard

Each Arr container can run its own dashboard instance. Publish the dashboard port
from that container, then enable it in `/config/extended.conf`:

```bash
enableDashboard="true"
dashboardPort="8687" # Lidarr; Sonarr 8990, Radarr 7879, Readarr 8788
dashboardUsername="your_username"
dashboardPassword="your_password"
```

Restart twice after first installing the dashboard service. Open the mapped port
in a browser. Lidarr currently provides task controls for ReplayGain, lyrics,
ratings, release-group repair, and library audits. Sonarr, Radarr, and Readarr
initially provide the shared log viewer while their task adapters are added.
Long jobs continue when the browser closes. Write tasks that can affect a broad
part of the library require a recent successful preview.

## QueueCleaner

Enable it in `/config/extended.conf`:

```bash
enableQueueCleaner="true"
queueCleanerScriptInterval="15m"
```

It periodically checks the app's download queue and removes eligible stuck/failed
items so they can be searched again. Soulseek imports are handled by their own
worker. Additional options, including torrent-category handling, are in each
app's configuration template: [Lidarr](../lidarr/extended.conf),
[Sonarr](../sonarr/extended.conf), [Radarr](../radarr/extended.conf), or
[Readarr](../readarr/extended.conf).

## Recyclarr

For Sonarr or Radarr, enable it in `/config/extended.conf`:

```bash
enableRecyclarr="true"
recyclarrScriptInterval="6h"
recyclarrConfig="/config/extended/recyclarr.yaml"
```

Review the supplied [Sonarr configuration](../sonarr/recyclarr.yaml) or
[Radarr configuration](../radarr/recyclarr.yaml) before enabling. The sync can
replace existing custom formats and reset scores. To keep your own configuration,
set `recyclarrConfig` to a separate YAML file. Recyclarr configures download
preferences; it does not download media itself.

## Soulseek downloads

The optional shared background worker connects Lidarr, Sonarr and Radarr to
[slskd](https://github.com/slskd/slskd) or Nicotine+ with the
[HTTP API plugin](https://github.com/SickProdigy/nicotineplus-plugins). **slskd 0.26.0 or newer is required**;
its batch API assigns a separate download directory to every job and lets the
worker reconcile uncertain responses after a restart. The worker checks the
version and Soulseek connection before starting searches. Both clients are supported by the same matching/import worker.

Run slskd alongside your Arr containers. arr-scripts installs the worker into
each Arr container; it does not install or start slskd itself. SoulseekQt and
ordinary Nicotine+ installations do not expose this API. For Nicotine+, install the API plugin from the separate repository. The initial
supported target is Nicotine+ 3.3.11 with Python 3.9+. Set `soulseekClient="nicotine"`
and `soulseekUrl="http://nicotineplus:12339"`, with the plugin's key in `soulseekApiKey`.
Its API version is independent of slskd; the worker requires plugin version 0.1.3
and the advertised search and batch capabilities. Keep Nicotine+ running and
connected. You can continue using its GUI.

Start with the application guide for your configuration:
[Lidarr](../lidarr/readme.md#optional-soulseek-downloads), [Sonarr](../sonarr/readme.md#optional-soulseek-downloads), or
[Radarr](../radarr/readme.md#optional-soulseek-downloads). The sections below cover the shared behavior.

### Nicotine+ plugin settings

Install the `api` directory from the plugin repository, then enable
**Soulseek HTTP API** in Nicotine's plugin preferences. Set an API key of at
least 32 random characters. For separate containers, use listen address
`0.0.0.0` (the plugin default) and port `12339`; `127.0.0.1` cannot accept connections
from Arr's container. Use a private Docker network shared with Arr.

The **Completed download root** is an absolute path inside Nicotine. Use a
dedicated mount such as `/data/arr-scripts` for automated downloads, with the
same host directory mounted at `/downloads/soulseek` inside Arr. This preserves
your normal personal completed/incomplete download locations. A blank root
uses Nicotine's normal completed-download directory instead.

Save settings and disable/re-enable the plugin to start the listener with the
new values. An empty API key prevents startup. Use the same key in Arr and its
reachable Docker service name or alias in `soulseekUrl`. A shared Docker network
does not require publishing the API port to the host. Recreate containers after
adding mounts or changing networks.

### Configuration

In each application's `/config/extended.conf`:

```bash
enableSoulseek="true"
soulseekClient="slskd" # or nicotine
soulseekUrl="http://slskd:5030"
soulseekApiKey="YOUR_SLSKD_API_KEY"
soulseekDownloadPath="/downloads/soulseek"
```

Use a slskd API key with read/write permissions, or the Nicotine+ plugin API key.
Configure your Soulseek account in the selected client, not in arr-scripts. The worker reads the local Arr API configuration
from `/config/config.xml`; existing `arrUrl` and `arrApiKey` overrides also work.

The `soulseekDownloadPath` directory must be the **completed** downloads directory
configured in the selected client. Mount the same host directory at that path inside each Arr
container. The incomplete directory stays separate. Both containers need write
permissions for the same user/group. API connectivity alone is not sufficient:
the Arr application must also be able to read the actual downloaded files.

For Lidarr, select providers with `dlClientSource`:

| Value | Behavior |
| --- | --- |
| `deezer` | Deezer only (existing default) |
| `tidal` | TIDAL only |
| `both` | Deezer and TIDAL, preserving the original setting |
| `soulseek` | Soulseek only; no streaming-client setup or downloads |
| `all` | Deezer and TIDAL, then Soulseek when enabled |
| `deezer,soulseek` or `tidal,soulseek` | Selected streaming provider, then Soulseek |

Soulseek always requires `enableSoulseek="true"`, including when selected through
`all`. Comma-separated selection does not change the existing streaming-provider
search order. Existing ARL and TIDAL authentication requirements still apply.

When the Audio script is enabled, it writes a local fallback marker only after
the selected streaming providers miss an album. The Soulseek worker handles the
network requests separately. Previously cached streaming misses become eligible
after the fallback delay. If the streaming worker fails or is unavailable,
Soulseek can also take over after that delay. It waits while the streaming worker
is actively running, except for albums already marked as misses. Audio checks
local active Soulseek jobs before starting another streaming download. With Audio disabled, the wanted-list worker uses the
delay instead. Soulseek-only music searches do not wait for streaming providers.

For Sonarr and Radarr, enabling Soulseek adds a fallback for monitored missing,
already released items. `soulseekFallbackDelay="3600"` gives normal download
sources one hour from when the worker first sees an item. Set it to `0` to search
immediately. Items already in the application's download queue are skipped.
This is a delay-based fallback; it does not run or prove exhaustion of every
configured indexer search.

The worker reads ten wanted items per pass and stores its page cursor in the
job database, continuing from that point after a restart. It revisits the first
page after reaching the end, so newly missing items are picked up on later
passes. Existing jobs are refreshed separately from discovery; changing pages
does not complete or abandon a download. One newly eligible streaming
miss marker is checked directly each pass, even outside the current page.
Only one Soulseek job can actively search, download, or import at a time per
application; checking ten wanted items does not enqueue ten downloads. Progress logs
show the wanted page, number of items checked, and eligible targets.

### Example slskd service

Add slskd to the Docker network used by the relevant Arr containers. Merge this
service into your Compose configuration and choose your own persistent paths:

```yaml
services:
  slskd:
    image: slskd/slskd:0.26.0
    restart: unless-stopped
    environment:
      PUID: "1000"
      PGID: "1000"
      SLSKD_SLSK_USERNAME: ${SLSKD_SLSK_USERNAME:?Set your Soulseek username}
      SLSKD_SLSK_PASSWORD: ${SLSKD_SLSK_PASSWORD:?Set your Soulseek password}
      SLSKD_API_KEY: ${SLSKD_API_KEY:?Set a read/write API key}
      SLSKD_DOWNLOADS_DIR: /downloads/soulseek
      SLSKD_INCOMPLETE_DIR: /downloads/incomplete
    volumes:
      - ./slskd:/app
      - /your/downloads/soulseek:/downloads/soulseek
      - /your/downloads/incomplete:/downloads/incomplete
      - /your/shared/files:/shares:ro
    ports:
      - "127.0.0.1:5030:5030"
      - "50300:50300"
```

Also mount `/your/downloads/soulseek:/downloads/soulseek` into each participating
Arr container. Configure shared folders in slskd. Follow the upstream
[Docker instructions](https://github.com/slskd/slskd/blob/master/docs/docker.md)
and [configuration reference](https://github.com/slskd/slskd/blob/master/docs/config.md)
for web credentials, sharing, API key roles and port forwarding.

Local tests use simulated API responses. Live Soulseek transfers and deployed
Arr import acceptance have not yet been validated.

<details>
<summary>Matching, import behavior, audio quality, and recovery</summary>

### Import behavior and limits

This is a background wanted-list downloader, not a native Torznab indexer or
an entry in the application's Download Clients settings. No Lidarr plugin branch
is required. It currently fills missing items; it does not perform upgrades.

Music matching requires an unambiguous selected release and a complete matching
track list from one peer. Movies require matching title/year filenames. TV
requires standard `SxxExx` naming; daily episodes, anime absolute numbering and
season packs are skipped. Conservative matching can miss valid releases.

#### Lidarr imports

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
release group. When Lidarr has none, the worker also checks the original remote
folder and filenames for a built-in list of common music release groups. Add
names with `soulseekReleaseGroups`; the default `soulseekReleaseGroupMode="extend"`
keeps the built-ins, while `replace` treats your list as the complete allowlist.
Library filenames show the selected group when it is included in Lidarr's
renaming format. There is only one completed
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

#### Sonarr and Radarr imports

Completed downloads are isolated under `<batch UUID>` inside
the shared download directory, with no extra app-name or `arr-scripts` folders.
A dedicated per-application root can be used if needed; UUIDs keep shared-root
batches distinct. Existing jobs retain their original destination until finished
or discarded. The worker checks successful transfer states and
file sizes, requests the application's manual-import decisions, and submits
only matching identities without import rejections. A release group recognized
by the application is preserved; otherwise the import uses `Soulseek` as the
release group. Include the release-group field in the application's renaming
format to show it in library filenames. It never forces a rejected
import or automatically replaces existing files. Import uses copy mode. Once the
import command succeeds, the item leaves the wanted list, and the library files
are accessible, the worker deletes only its recorded batch source files. Personal
downloads and unrecognized files are untouched; empty batch folders are removed.
Unavailable library files, changed sources, or uncertain imports retain downloads
and retry cleanup after five minutes. Filename matching happens before downloading; full file
quality/language/custom-format decisions happen after downloading and may reject
the result. Native import notifications continue to run configured Arr scripts.

Jobs live in `/config/extended/soulseek/jobs.sqlite`. A process lock prevents
overlapping workers. For a read-only connection and wanted-list check, run
`ARR_SOULSEEK_APP=lidarr bash /config/extended/Soulseek.bash --diagnose`
(replace the app as needed). This bypasses the worker lock, prints no credentials,
and does not start searches or downloads.

#### Failed candidates and background downloads

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

`soulseekMaxForegroundBatches` defaults to **3 active searches/download batches
per app** and accepts values from 1 through 10. `soulseekMaxBatches` limits the
combined foreground and offline-background total, also defaulting to 3. Review
jobs are terminal and do not consume either limit. These settings limit album
batches, not individual track files, and are not attempts-per-day limits. State,
candidate identity, and offline timestamps survive restarts. If the foreground
setting is absent, it inherits `soulseekMaxBatches` for compatibility with
existing configurations.

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

API failures defer for five minutes. Searches and download batches have durable IDs; uncertain requests are reconciled instead of blindly resubmitted.

#### Manual recovery

To discard a reviewed job and its finished batch files:

```bash
ARR_SOULSEEK_APP=lidarr bash /config/extended/Soulseek.bash --discard 123
```

Replace the application and job ID as needed. Discard verifies batch ownership,
finished transfers, and any acknowledged import command before deleting only
recorded files from that UUID batch. Personal files, changed or shared files,
active transfers, and uncertain imports are retained. The job becomes
`discarded`, preventing automatic re-download; `--retry 123` starts it afresh.
Discard itself never starts another search. Batch history remains in the client.

If you already cancelled and cleared a queued batch in Nicotine's GUI, use
`--discard ITEM_ID --cancelled` to clean its recorded files and release the
worker slot. This also accepts jobs still marked downloading/enqueueing in the
worker. The flag confirms cancellation you performed; it does not cancel remote
transfers. It retains all ownership/path checks and will not discard uncertain
imports or albums handed to Lidarr's Activity queue.

Partial batches, ambiguous imports and uncertain import replies enter `review`
state and are logged by item ID. Cleanup failures enter review after three attempts;
completed or untracked handoffs enter review after one hour without resolution.
Files are preserved, and unrelated searches can continue within the batch limits.
Nicotine failures use coordinated cancellation
and candidate retry; unsupported client cleanup retains files for review. After fixing
the cause, use `ARR_SOULSEEK_APP=lidarr bash /config/extended/Soulseek.bash --retry 123`
(substitute the app and item ID). A retried batch is checked again before import;
failed batches require resolving their transfers in slskd first. No global
Soulseek transfers or files are cleared by the worker.

### Lidarr audio format and quality

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

</details>
