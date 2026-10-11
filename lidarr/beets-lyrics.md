# Lyrics migration

Lyrics now use an independent background worker that calls LRCMux directly.
Beets handles metadata only. See [background lyrics setup](lyrics.md) for
configuration, embedding, cache/retry behavior, and queue inspection.

Older `enableBeetsLyrics` / `beetsLyricsTimeout` settings are still accepted when
the new `enableLyrics` / `lyricsTimeout` settings are absent. The former synchronous
Beets lyric phase has been replaced by local enqueue work and a separate service.
