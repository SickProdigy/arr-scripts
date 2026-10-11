# Background lyrics through LRCMux

Beets matches and tags metadata; it does not fetch lyrics. Audio and BeetsTagger
disable its lyrics plugin even with older custom configs. After successful tagging,
they enqueue affected files locally and return. A separate `Lyrics` service calls
[LRCMux](https://github.com/f1nniboy/lrcmux) directly, so slow providers do not hold
up downloads or Lidarr imports. No extra custom-script registration is required.

## Configuration

In `/config/extended.conf`:

```bash
enableLyrics="true"
lyricsTimeout="3m"
lyricsUrl="https://api.lrcmux.dev"
```

The public endpoint needs no API key or additional container. An optional
`lyricsUrl` override supports a custom/self-hosted LRCMux instance. LRCMux handles
its provider aggregation; Beets' `lyrics.sources` no longer controls this helper.

`lyricsTimeout` bounds each background worker invocation, not the importer.
The service checks every 30 seconds, processes up to ten due tracks sequentially,
and allows only one worker at a time. Each lyric request has a 15-second socket
timeout; Arr path lookups have a ten-second timeout. The outer timeout also bounds
slow/trickling responses and file work. Import hooks do local queue work only,
with a 15-second safety limit and nonfatal errors.

Existing `enableBeetsLyrics` and `beetsLyricsTimeout` settings are accepted when
the new names are absent. New settings take precedence. Disabled mode performs
no lyric network requests. The old `BeetsLyrics.bash` is a compatibility enqueue
entry point; it no longer invokes `beet lyrics`.

## File handling

- Existing nonempty plain or synchronized embedded lyrics are preserved without
  another request, including MP3 USLT/SYLT frames.
- Existing same-stem `.lrc`, `.lyrics`, and `.txt` files are reused when audio
  tags are empty. Original sidecars are retained. Queuing captures local lyrics
  so they remain usable if Lidarr moves the audio file before the worker runs.
- New results are embedded in supported formats. MP3 uses USLT, M4A uses its
  lyrics atom, and FLAC/Opus use lyric comments through MediaFile. Line timestamps
  are retained as LRC text; player support for synchronized text in these tags
  varies. Native MP3 SYLT frames are preserved but are not newly generated.
- Unsupported embedding can fall back to a new sidecar without overwriting an
  existing file. Unreadable files or files lacking safe identifying metadata are
  skipped rather than guessed.

Before fetching and again before writing, the worker checks identity and existing
lyrics. It resolves moved downloads through Lidarr's current album track-file
paths and accepts only one matching file under the music root. Files still waiting
for import are deferred. The default root is `/music`; set the advanced
`lyricsLibraryRoot` option if your library uses another container path.

Writes are staged next to the target, rechecked against file changes, and replaced
atomically. Detected concurrent tag edits cause a retry instead of overwriting
them. File modification times update after embedding so library scanners can
detect new tags. ReplayGain, ratings, and MusicBrainz workers remain independent.

## Queue, caching, and recovery

State lives in `/config/extended/lyrics/jobs.sqlite`. Duplicate events at the same
path/identity are coalesced. Successful lookups and instrumental results are cached
for 30 days; no-result responses for one day. Populated files are skipped even
after cache expiry. Returned artist/title/album/duration must match conservatively;
valid alternate editions can be missed rather than attached to the wrong track.

Temporary failures use exponential backoff. HTTP 429 honors `Retry-After` and pauses
the whole worker until that time; it is not treated as missing lyrics. After eight
failed attempts, a job enters `review`. Rate limits and files waiting for their
final path do not exhaust provider retries. Queue state and cached successes
survive restarts, so completed tracks remain completed and interrupted work can
resume. A forced stop may leave a staged `.arr-lyrics-*` file; it does not replace
the original audio file or clear personal files.

Check jobs or retry a reviewed job without submitting a lyric request:

```bash
docker exec lidarr bash /config/extended/Lyrics.bash --status
docker exec lidarr bash /config/extended/Lyrics.bash --retry JOB_ID
```

Worker output uses the `Lyrics:` prefix in container logs. Import logs report
local enqueue work. For existing libraries, enqueue a chosen album directory:

```bash
docker exec lidarr bash /config/extended/Lyrics.bash '/music/Artist/Album'
```

Install/update using the [Lidarr instructions](readme.md), including the service
restart steps. Existing configurations are preserved. Offline tests cover real
audio tags and simulated providers; live LRCMux requests and deployed service
acceptance remain separate validation steps.
