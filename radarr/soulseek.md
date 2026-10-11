# Soulseek for Radarr

Install/update the scripts using the [Radarr README](readme.md). Choose slskd
**0.26.0+** or Nicotine+ with the separate API plugin using the
[shared client setup guide](../universal/soulseek.md).

## Downloads and connectivity

Use `/downloads/soulseek` as the standard completed-download mount inside Radarr.
For Nicotine+, share a dedicated host directory:

```yaml
# Nicotine container volumes
- /your/downloads/nicotineplus/arr-scripts:/data/arr-scripts
# Radarr container volumes
- /your/downloads/nicotineplus/arr-scripts:/downloads/soulseek
```

Set the Nicotine plugin's **Completed download root** to `/data/arr-scripts`.
For slskd, share its configured completed-download root with Radarr instead.
Keep incomplete files separate. Both containers need read/write permissions and
a shared Docker network. Recreate containers after adding mounts.

## extended.conf

For Nicotine+:

```bash
enableSoulseek="true"
soulseekClient="nicotine"
soulseekUrl="http://nicotineplus:12339"
soulseekApiKey="YOUR_PLUGIN_API_KEY"
soulseekDownloadPath="/downloads/soulseek"
soulseekFallbackDelay="3600"
```

For slskd, replace the client, URL and key:

```bash
soulseekClient="slskd"
soulseekUrl="http://slskd:5030"
soulseekApiKey="YOUR_READ_WRITE_SLSKD_API_KEY"
```

Use the client's actual Docker service name or network alias in the URL. The
Nicotine plugin needs a key of at least 32 random characters, listen address
`0.0.0.0`, and port `12339`. Disable/re-enable it after saving settings. Keep the
client running and connected, with API access on your private network.

## Fallback and cleanup

The worker searches for monitored, released missing movies after the delay;
set it to `0` for immediate searches. Queued movies are skipped. This delay gives
normal sources time to work; it does not prove every indexer has been exhausted.
Radarr does not use Lidarr's `dlClientSource` setting.

Matching requires movie title/year and acceptance by Radarr's native import
decisions. Existing files are not upgraded. After confirmed success and
library-file verification, owned downloaded files are deleted. Failed/uncertain
imports and personal downloads remain untouched. See the
[shared guide](../universal/soulseek.md) for recovery and validation status.
