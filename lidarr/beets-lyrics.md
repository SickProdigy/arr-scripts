# Beets lyrics

Metadata tagging runs with the lyrics plugin disabled, including with older custom
Beets configs. After a successful Beets import, Audio and BeetsTagger automatically
run a separate lyrics phase. ReplayGain is independent.

```bash
enableBeetsLyrics="true"
beetsLyricsTimeout="3m"
```

These settings belong in `/config/extended.conf`. Existing installations default
to enabled with a three-minute budget if the settings are absent. Set the enable
flag to `false` to disable this phase; metadata import still makes no lyric
requests. Setup preserves existing configuration files.

New lyrics are embedded into audio tags, not saved as new sidecars. Files with
existing plain or synchronized lyrics are skipped without provider requests.
Nonempty existing `.lrc`, `.lyrics` or same-stem `.txt` files and Beets database
lyrics are embedded locally when audio tags are empty. Original sidecars are
retained. Unreadable files are skipped instead of risking replacement. Known
instrumental items in the Beets database are also skipped.

Provider order remains in each Beets config's `lyrics.sources`. The helper forces
`force: no`, `keep_synced: yes`, `fallback: null` and `import.write: yes` in a
private temporary config, preserving your saved config. It selects exact Beets
item IDs within the processed directory, including disc subdirectories.

The timeout covers the whole optional phase, not each provider or song. A hung
process is terminated, with up to one second for forced shutdown. Successful
earlier writes remain embedded. Provider output, no-result messages, elapsed time,
exit status and timeout status go to the usual album log. HTTP 429 means the
provider is rate limiting requests; it does not mean lyrics do not exist.
Failures and timeouts do not fail metadata tagging or the Lidarr import.

Later imports or retags may retry tracks still missing lyrics. Tracks already
populated are skipped; no persistent failed-lookup cooldown is implemented.

See the [Beets lyrics documentation](https://beets.readthedocs.io/en/stable/plugins/lyrics.html).
