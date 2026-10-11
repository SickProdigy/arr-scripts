"""Lidarr audio preferences and safe staging conversion for Soulseek."""
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile


class AudioQualityError(ValueError):
    pass


def number(value):
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError):
        return 0


class AudioPolicy:
    def __init__(self, format="native", bitrate="master", strict=False):
        self.format, self.bitrate, self.strict = format, str(bitrate), strict
        if format not in ("native", "mp3", "aac", "opus", "alac"):
            raise ValueError("Invalid audioFormat")
        if format == "native":
            if self.bitrate not in ("master", "lossless", "high", "low"):
                raise ValueError("Invalid native audioBitrate")
        elif not self.bitrate.isdecimal() or not 32 <= int(self.bitrate) <= 512:
            raise ValueError("Non-native audioBitrate must be numeric")

    @property
    def config(self):
        return dict(format=self.format, bitrate=self.bitrate, strict=self.strict)

    def rank(self, file):
        extension = PureWindowsPath(file["filename"]).suffix.lower()
        rate = number(file.get("bitRate"))
        depth, sample = number(file.get("bitDepth")), number(file.get("sampleRate"))
        # FLAC is unambiguous. M4A may be AAC or ALAC; peer attributes are only
        # a hint and actual codec checks remain mandatory before strict import.
        lossless = extension == ".flac" or extension == ".m4a" and depth >= 16 and rate >= 500
        if "codec" in file:
            lossless = file["codec"] in ("flac", "alac")
        hires = lossless and (depth > 16 or sample > 48000)
        if self.format == "native":
            if self.bitrate in ("master", "lossless"):
                required = hires if self.bitrate == "master" else lossless
                if self.strict and not required:
                    return None
                tier = 0 if hires and self.bitrate == "master" else 1 if lossless else 2
                return tier, -depth if lossless else -rate, -sample if lossless else 0
            desired = 320 if self.bitrate == "high" else 128
            minimum = (256 if extension == ".m4a" else 320) if self.bitrate == "high" else 96 if extension == ".m4a" else 128
            if self.strict and (lossless or not rate or rate < minimum):
                return None
            return (1 if lossless else 0, abs(rate-desired) if rate else 10000, -rate)
        # Prefer lossless input for requested conversion, then an already
        # matching lossy format. Never convert an inferior lossy source upward.
        expected = {"mp3":".mp3", "aac":".m4a", "opus":".opus", "alac":".m4a"}[self.format]
        matching = extension == expected and self.format != "alac" and not lossless
        if "codec" in file:
            matching = file["codec"] == self.format and not lossless
        if self.strict and not lossless and (not matching or rate < int(self.bitrate)):
            return None
        return (0 if lossless else 1 if matching else 2, -depth if lossless else -rate, -sample if lossless else 0)

    def choose_rank(self, files):
        ranks = [self.rank(file) for file in files]
        return None if any(rank is None for rank in ranks) else max(ranks)

    def probe(self, path):
        try:
            result = subprocess.run([os.environ.get("SOULSEEK_FFPROBE", "ffprobe"), "-v", "error", "-select_streams", "a:0",
                                     "-show_entries", "stream=codec_name,sample_rate,bits_per_raw_sample,bits_per_sample,bit_rate:format=bit_rate",
                                     "-of", "json", str(path)], capture_output=True, timeout=30, check=True)
            data = json.loads(result.stdout)
            stream = data["streams"][0]
            # A container bitrate includes artwork and tags; it is not proof
            # of the audio bitrate when the stream doesn't provide one.
            return dict(filename=path.name, codec=stream["codec_name"],
                        bitRate=number(stream.get("bit_rate"))//1000,
                        bitDepth=number(stream.get("bits_per_raw_sample")) or number(stream.get("bits_per_sample")),
                        sampleRate=number(stream.get("sample_rate")))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
            raise AudioQualityError("Cannot verify audio quality") from None

    def prepare(self, folder, files):
        temporary_files = []
        try:
            return self._prepare(folder, files, temporary_files)
        finally:
            for temporary in temporary_files:
                temporary.unlink(missing_ok=True)

    def _prepare(self, folder, files, temporary_files):
        if self.format == "native" and not self.strict:
            return [dict(file, sourceFilename=file["filename"]) for file in files]
        outputs, pending = [], []
        for file in files:
            source = folder/PureWindowsPath(file["filename"]).name
            metadata = self.probe(source)
            if self.strict and self.rank(metadata) is None:
                raise AudioQualityError("Downloaded audio does not meet requested quality")
            lossless = metadata["codec"] in ("flac", "alac")
            convert = self.format != "native" and lossless and metadata["codec"] != self.format
            if not convert:
                outputs.append(dict(file, sourceFilename=file["filename"]))
                continue
            extension = {"mp3":".mp3", "aac":".m4a", "opus":".opus", "alac":".m4a"}[self.format]
            output = source.with_suffix(extension)
            if output.is_symlink() or (output != source and output.exists()):
                raise AudioQualityError("Output filename already exists")
            descriptor, name = tempfile.mkstemp(prefix=".audio-", suffix=extension, dir=folder)
            os.close(descriptor)
            temporary = Path(name)
            temporary_files.append(temporary)
            try:
                codec = {"mp3":"libmp3lame", "aac":"aac", "opus":"libopus", "alac":"alac"}[self.format]
                args = [os.environ.get("SOULSEEK_FFMPEG", "ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(source),
                        "-map", "0:a:0", "-map_metadata", "0", "-c:a", codec]
                # Opus cannot carry an attached image stream. Other outputs
                # preserve attached cover art without re-encoding it.
                if self.format != "opus":
                    args += ["-map", "0:v?", "-c:v", "copy"]
                if self.format != "alac":
                    args += ["-b:a", self.bitrate+"k"]
                if self.format == "mp3":
                    args += ["-id3v2_version", "3"]
                if self.format in ("aac","alac"):
                    args += ["-movflags", "+faststart"]
                subprocess.run(args+[str(temporary)], capture_output=True, timeout=600, check=True)
                actual = self.probe(temporary)
                if actual["codec"] != {"mp3":"mp3","aac":"aac","opus":"opus","alac":"alac"}[self.format] or temporary.stat().st_size <= 0:
                    raise AudioQualityError("Converted audio verification failed")
                pending.append((temporary, output))
                outputs.append(dict(filename=output.name, size=temporary.stat().st_size, sourceFilename=file["filename"]))
            except (OSError, subprocess.SubprocessError):
                raise AudioQualityError("Audio conversion failed; original retained") from None
        for temporary, output in pending:
            os.replace(temporary, output)
        # Only staged copies are removed, and only after the whole album has
        # converted. The downloader's original batch remains intact on failure.
        for file in outputs:
            source = folder/PureWindowsPath(file["sourceFilename"]).name
            if source.name != PureWindowsPath(file["filename"]).name:
                source.unlink()
        return outputs
