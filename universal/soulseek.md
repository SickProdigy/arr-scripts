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
Its API version is independent of slskd; the worker requires plugin version 0.1.0
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

## Import behavior and limits

This is a background wanted-list downloader, not a native Torznab indexer or
an entry in the application's Download Clients settings. No Lidarr plugin branch
is required. It currently fills missing items; it does not perform upgrades.

Music matching requires an unambiguous selected release and a complete matching
track list from one peer. Movies require matching title/year filenames. TV
requires standard `SxxExx` naming; daily episodes, anime absolute numbering and
season packs are skipped. Conservative matching can miss valid releases.

Completed downloads are isolated under `arr-scripts/<app>/<batch UUID>` inside
the shared download directory. The worker checks successful transfer states and
file sizes, requests the application's manual-import decisions, and submits
only matching identities without import rejections. It never forces a rejected
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

At most three jobs actively search/download/import per
application. Search misses wait one day; API failures defer for five minutes.
Searches and download batches have durable IDs; uncertain download requests are
reconciled rather than blindly submitted again.

Failed transfers, partial batches, ambiguous imports and uncertain import replies
enter `review` state and are logged by item ID. Files are retained. After fixing
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
