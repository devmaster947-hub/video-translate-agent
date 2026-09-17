"""Media primitives only: probe, source extraction and WAV normalization."""
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from .config import MediaConfig, PROJECT_ROOT, anchored_path


class MediaError(RuntimeError):
    """A stable error code; raw subprocess output is never put in a manifest."""


def executable(name: str) -> str:
    if Path(name).is_absolute() or "/" in name or "\\" in name:
        candidate = anchored_path(name)
        if not candidate.is_file():
            raise MediaError("TOOL_NOT_FOUND")
        return str(candidate)
    found = shutil.which(name)
    if not found:
        raise MediaError("TOOL_NOT_FOUND")
    return str(Path(found).resolve())


_CONFIGURED_TIMEOUT = object()


class Media:
    def __init__(self, config: MediaConfig):
        self.config = config

    def run(self, tool: str, arguments: list[str], *, cwd: Path = PROJECT_ROOT,
            timeout_seconds=_CONFIGURED_TIMEOUT) -> str:
        if not cwd.is_absolute():
            raise MediaError("CWD_MUST_BE_ABSOLUTE")
        try:
            result = subprocess.run(
                [executable(tool), *map(str, arguments)], shell=False, check=True,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=(self.config.timeout_seconds if timeout_seconds is _CONFIGURED_TIMEOUT
                         else timeout_seconds), cwd=str(cwd),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as exc:
            raise MediaError("MEDIA_TIMEOUT") from exc
        except (subprocess.CalledProcessError, OSError) as exc:
            raise MediaError("MEDIA_EXECUTION_FAILED") from exc
        return result.stdout

    def probe(self, path: Path) -> dict:
        if not path.is_absolute() or not path.is_file():
            raise MediaError("MEDIA_FILE_NOT_FOUND")
        raw = self.run(self.config.ffprobe,
                       ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
        try:
            data = json.loads(raw)
            streams = data["streams"]
            duration = float(data.get("format", {}).get("duration", 0))
            if not streams or not isinstance(streams, list) or not math.isfinite(duration) or duration <= 0:
                raise ValueError()
            return {"duration_ms": round(duration * 1000), "streams": streams}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise MediaError("INVALID_MEDIA_METADATA") from exc

    def require_video(self, path: Path) -> dict:
        info = self.probe(path)
        if not any(item.get("codec_type") == "video" and not item.get("disposition", {}).get("attached_pic")
                   for item in info["streams"]):
            raise MediaError("VIDEO_STREAM_REQUIRED")
        return info

    def _convert(self, source: Path, output: Path, options: list[str], validate) -> Path:
        if not source.is_absolute() or not output.is_absolute():
            raise MediaError("MEDIA_PATH_MUST_BE_ABSOLUTE")
        if source.resolve() == output.resolve() or (output.exists() and source.samefile(output)):
            raise MediaError("SOURCE_OVERWRITE_FORBIDDEN")
        if output.exists():
            raise MediaError("OUTPUT_ALREADY_EXISTS")
        self.probe(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.stem}-{uuid4().hex}.tmp{output.suffix}")
        try:
            self.run(self.config.ffmpeg,
                     ["-hide_banner", "-nostdin", "-v", "error", "-n", "-i", str(source),
                      *options, str(temporary)], cwd=output.parent)
            validate(self.probe(temporary))
            # Hard link publication is atomic and refuses to overwrite an existing destination.
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return output

    def extract_audio(self, source: Path, output: Path) -> Path:
        """Source audio for future ASR, not a track for final output."""
        return self._wav(source, output, 16000, 1)

    def normalize_audio(self, source: Path, output: Path) -> Path:
        return self._wav(source, output, 48000, 2)

    def _wav(self, source: Path, output: Path, rate: int, channels: int) -> Path:
        if output.suffix.lower() != ".wav":
            raise MediaError("WAV_OUTPUT_REQUIRED")

        def validate(info):
            streams = info["streams"]
            if (len(streams) != 1 or streams[0].get("codec_name") != "pcm_s16le"
                    or int(streams[0].get("sample_rate", 0)) != rate
                    or streams[0].get("channels") != channels):
                raise MediaError("INVALID_WAV_FORMAT")

        return self._convert(source, output,
                             ["-map", "0:a:0", "-vn", "-sn", "-dn", "-ar", str(rate),
                              "-ac", str(channels), "-c:a", "pcm_s16le"], validate)

    def video_without_audio(self, source: Path, output: Path) -> Path:
        """Copy only the first video stream; discard every source audio stream."""
        if output.suffix.lower() != ".mp4":
            raise MediaError("MP4_OUTPUT_REQUIRED")
        self.require_video(source)

        def validate(info):
            if len(info["streams"]) != 1 or info["streams"][0].get("codec_type") != "video":
                raise MediaError("VIDEO_ONLY_OUTPUT_REQUIRED")

        return self._convert(source, output,
                             ["-map", "0:v:0", "-an", "-sn", "-dn", "-c:v", "copy"], validate)

    def capabilities(self) -> dict:
        result = {"ffmpeg": False, "ffprobe": False, "subtitles": False,
                  "libx264": False, "aac": False, "atempo": False,
                  "rubberband": bool(shutil.which("rubberband"))}
        errors = []
        for key in ("ffmpeg", "ffprobe"):
            try:
                self.run(getattr(self.config, key), ["-version"])
                result[key] = True
            except MediaError:
                errors.append(f"{key.upper()}_UNAVAILABLE")
        if result["ffmpeg"]:
            try:
                filters = self.run(self.config.ffmpeg, ["-hide_banner", "-filters"])
                encoders = self.run(self.config.ffmpeg, ["-hide_banner", "-encoders"])
                names = lambda text: {line.split()[1] for line in text.splitlines() if len(line.split()) >= 2}
                result.update(subtitles="subtitles" in names(filters), atempo="atempo" in names(filters),
                              libx264="libx264" in names(encoders), aac="aac" in names(encoders))
            except MediaError:
                errors.append("FFMPEG_CAPABILITY_CHECK_FAILED")
        for capability in ("subtitles", "atempo", "libx264", "aac"):
            if not result[capability]:
                errors.append(f"{capability.upper()}_UNAVAILABLE")
        return {"capabilities": result, "errors": errors}
