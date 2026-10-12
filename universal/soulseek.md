# Soulseek downloads

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
[Lidarr](../lidarr/soulseek.md), [Sonarr](../sonarr/soulseek.md), or
[Radarr](../radarr/soulseek.md). The sections below cover the shared behavior.

## Nicotine+ plugin settings

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

## Configuration

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

## Import behavior and limits

This is a background wanted-list downloader, not a native Torznab indexer or
an entry in the application's Download Clients settings. No Lidarr plugin branch
is required. It currently fills missing items; it does not perform upgrades.

Music matching requires an unambiguous selected release and a complete matching
track list from one peer. Movies require matching title/year filenames. TV
requires standard `SxxExx` naming; daily episodes, anime absolute numbering and
season packs are skipped. Conservative matching can miss valid releases.

For Lidarr, completed albums use the existing Usenet Blackhole client watching
`importPath`, matching the Tidal/Deezer Activity queue workflow. Publication is
atomic: incomplete audio never enters that folder. Soulseek-owned rejection
recovery records failed candidates and coordinates client cleanup before trying
another copy; successful imports disappear. Uncertain ownership retains files. See the [Lidarr guide](../lidarr/soulseek.md). The remaining direct-import
behavior below applies to Sonarr/Radarr and older reviewed Lidarr jobs.

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

One foreground selection runs per application. With Nicotine API 0.1.3+, a peer
continuously offline for 15 minutes can remain queued in the background while
another candidate or album proceeds. The default cap is 3 outstanding batches
per app, including background and retained uncertain copies. Offline copies
get 24 hours to recover; a returning peer resets that clock. Failed peer/folder
candidates are excluded for 30 days. Slow online downloads retain their slot.
The limit counts album batches, not tracks; one album can contain many files.
Tidal and Deezer continue independently. Initial search misses use retryNotFound;
exhausted failed candidates use failedDownloadRetryDays before a new search.
API failures defer for five minutes. See the [Lidarr guide](../lidarr/soulseek.md)
for settings and cancellation safeguards.
Searches and download batches have durable IDs; uncertain download requests are
reconciled rather than blindly submitted again.

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
state and are logged by item ID. Nicotine failures use coordinated cancellation
and candidate retry; unsupported client cleanup retains files for review. After fixing
the cause, use `ARR_SOULSEEK_APP=lidarr bash /config/extended/Soulseek.bash --retry 123`
(substitute the app and item ID). A retried batch is checked again before import;
failed batches require resolving their transfers in slskd first. No global
Soulseek transfers or files are cleared by the worker.

## Example slskd service

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
