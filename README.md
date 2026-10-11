# arr-scripts

Official Home of the scripts that were previously packaged with the "Extended" containers. Designed to be easily implemented/added to [Linuxserver.io](https://www.linuxserver.io/) containers.

## Usage

- [Radarr Instructions](https://github.com/SickProdigy/arr-scripts/tree/main/radarr/readme.md)
- [Sonarr Instructions](https://github.com/SickProdigy/arr-scripts/tree/main/sonarr/readme.md)
- [Lidarr Instructions](https://github.com/SickProdigy/arr-scripts/tree/main/lidarr/readme.md)
- [Readarr Instructions](https://github.com/RandomNinjaAtk/arr-scripts/tree/main/readarr/readme.md)
- [SABnzbd Instructions](https://github.com/RandomNinjaAtk/arr-scripts/tree/main/sabnzbd#/readme.md)

## WARNING

DO NOT USE PORTAINER, it is known to not work and typically breaks things... From the various discussions I've seen in the Arr communities and Linuxserver.io, they tend to trend on saying to avoid it... If your using portainer and this is not functioning as expected, that is likely your problem.... (Example: <https://github.com/RandomNinjaAtk/arr-scripts/discussions/57>)

Also see here: <https://wiki.servarr.com/docker-guide#portainer>

## Support Info

Scripts are provided as-is... Generally, if a script works one time, it will work everytime, that is the nature of scripts... So if you're experiencing an issue that has not been previously reported and is more likely a technical problem of some sort, it is more than likely caused by user error...

Please note that use of arr-scripts is not supported by the Arr app's community. The scripts do not modify the software/code of the Arr app, all changes to the Arr app are implemented using publicly available API calls.

## Optional Soulseek downloads

Lidarr, Sonarr and Radarr can use Soulseek for monitored missing items. Choose
one client below and run it alongside your Arr containers. Install or update
arr-scripts using the application's instructions above so the Soulseek worker
is installed. You do not add this worker to Arr's Download Clients settings.

### Option 1: slskd

1. Run [slskd](https://github.com/slskd/slskd) **0.26.0 or newer**, configure your
   Soulseek account, and create an API key with read/write permissions.
2. Put slskd and the participating Arr containers on a shared Docker network.
   Mount slskd's completed-download directory into each Arr container at
   `/downloads/soulseek`. Keep incomplete downloads separate and give both
   containers permission to read/write the completed directory.
3. Add these settings to each Arr application's `/config/extended.conf`:

   ```bash
   enableSoulseek="true"
   soulseekClient="slskd"
   soulseekUrl="http://slskd:5030"
   soulseekApiKey="YOUR_SLSKD_API_KEY"
   soulseekDownloadPath="/downloads/soulseek"
   ```

`slskd` in the URL must be its reachable Docker service name or network alias.
See the [slskd Compose example](universal/soulseek.md#example-slskd-service)
and upstream instructions for client configuration and port forwarding.

### Option 2: Nicotine+

1. Use **Nicotine+ 3.3.11 or newer with Python 3.9+**. Install the `api` folder
   from [nicotineplus-plugins](https://github.com/SickProdigy/nicotineplus-plugins)
   into Nicotine+'s plugin directory, including `PLUGININFO` and `__init__.py`.
   The initial compatibility checks cover Nicotine+ 3.3.11; newer versions
   require validation.
2. Enable **Soulseek HTTP API** in Nicotine+'s plugin preferences and open its
   settings. Set an API key of at least **32 random characters**. For separate
   Docker containers, set **Listen address** to `0.0.0.0` and **HTTP API port**
   to `12339`. The default `127.0.0.1` only accepts connections inside Nicotine's
   own host/container.
3. Set **Completed download root** to an absolute path inside the Nicotine
   container. A dedicated folder keeps automated downloads separate from your
   personal downloads. For example, mount the same host directory in both
   containers:

   ```yaml
   # Nicotine container volumes
   - /your/downloads/nicotine-arr:/data/arr-scripts
   # Each participating Arr container's volumes
   - /your/downloads/nicotine-arr:/downloads/soulseek
   ```

   With these mounts, set the plugin's root to `/data/arr-scripts` and Arr's
   `soulseekDownloadPath` to `/downloads/soulseek`. These container paths may
   differ; the host directory must be the same. Your normal Nicotine completed
   and incomplete directories can remain as they are. Leaving the plugin's
   root blank uses Nicotine's normal completed-download directory instead.
4. Save the plugin settings, then **disable and re-enable the plugin**. An empty
   API key prevents the listener from starting; settings changes require this
   restart. Keep Nicotine+ running and connected to Soulseek.
5. Put Nicotine and Arr on a shared Docker network and add the following to
   each Arr application's `/config/extended.conf`:

   ```bash
   enableSoulseek="true"
   soulseekClient="nicotine"
   soulseekUrl="http://nicotineplus:12339"
   soulseekApiKey="YOUR_PLUGIN_API_KEY"
   soulseekDownloadPath="/downloads/soulseek"
   ```

Use the same key as the plugin and replace `nicotineplus` with your container's
service name or network alias. Arr connects over the Docker network; publishing
port `12339` to the host is unnecessary for that connection. Keep API access on
a private network. Recreate containers after adding volume mounts or changing
their Docker networks. Your Soulseek account stays in Nicotine+, not in Arr.

### Provider selection and cleanup

For **Lidarr**, also set `dlClientSource` in `extended.conf`:

| Setting | Sources |
| --- | --- |
| `dlClientSource="soulseek"` | Soulseek only, without the fallback delay |
| `dlClientSource="all"` | Deezer/TIDAL with enabled Soulseek fallback |
| `dlClientSource="deezer,soulseek"` | Deezer with Soulseek fallback |
| `dlClientSource="tidal,soulseek"` | TIDAL with Soulseek fallback |

Selecting streaming providers still requires their usual setup. With `all`,
unavailable streaming clients do not automatically make Soulseek the first
source: fallback can wait `soulseekFallbackDelay="3600"` seconds. Confirmed
streaming misses become eligible immediately. Sonarr and Radarr use the delay
for monitored, released missing items and skip items already queued; set the
delay to `0` for immediate searches.

Soulseek uses its own tracked batches rather than Lidarr's Deezer/TIDAL
`downloadPath` and blackhole `importPath`. After a confirmed successful import
and verification of the library files, the worker removes its downloaded source
files, avoiding a retained second copy. Failed or uncertain imports are kept
for troubleshooting; personal downloads remain untouched.

See [Soulseek setup](universal/soulseek.md) for full fallback behavior, recovery,
and current matching limits. Live peer transfers and deployed Arr imports still
require validation.
