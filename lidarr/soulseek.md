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

Nicotine manages its own transfers; completed batches are imported through
Lidarr's API rather than placed in the Deezer/TIDAL blackhole import folder.
The worker verifies successful imports and accessible library files before
deleting its source files. Failed/uncertain imports and personal downloads are
retained. This avoids keeping a second copy after a verified import.

See the [shared guide](../universal/soulseek.md) for matching limits, fallback
timing, recovery commands, and validation status.
