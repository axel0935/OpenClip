"""ffprobe-based media inspection."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from openclip.core.config import Settings
from openclip.core.models import MediaInfo
from openclip.ingest.errors import IngestFailure

_PROBE_TIMEOUT_SECONDS = 120


def _run_probe(path: Path, settings: Settings) -> dict[str, Any]:
    """Run ffprobe and return its JSON document."""
    if not shutil.which(settings.ffprobe_path):
        raise IngestFailure(
            "ffmpeg_missing",
            f"ffprobe '{settings.ffprobe_path}' was not found on PATH; "
            "install FFmpeg, then run `openclip doctor`.",
        )
    cmd = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS, check=False
        )
    except FileNotFoundError as exc:
        raise IngestFailure(
            "ffmpeg_missing",
            f"ffprobe '{settings.ffprobe_path}' could not be executed; run `openclip doctor`.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise IngestFailure("unsupported_file", f"ffprobe timed out reading '{path}'.") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown error"
        raise IngestFailure("unsupported_file", f"ffprobe could not read '{path}': {detail}")
    try:
        data: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise IngestFailure(
            "unsupported_file", f"ffprobe returned invalid JSON for '{path}'."
        ) from exc
    return data


def _parse_fps(rate: str | None) -> float:
    """Parse an ffprobe frame-rate string such as '30000/1001'."""
    if not rate:
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(rate)
    except ValueError:
        return 0.0


def probe_media(path: Path, settings: Settings, source_url: str | None = None) -> MediaInfo:
    """Inspect a media file and return its :class:`MediaInfo` contract."""
    data = _run_probe(path, settings)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise IngestFailure("unsupported_file", f"'{path}' does not contain a video stream.")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _parse_fps(video.get("r_frame_rate")) or _parse_fps(video.get("avg_frame_rate"))
    if width <= 0 or height <= 0 or fps <= 0:
        raise IngestFailure(
            "unsupported_file",
            f"'{path}' has unusable video dimensions or frame rate.",
        )
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    tags = fmt.get("tags") or {}
    title = tags.get("title") or tags.get("TITLE")
    return MediaInfo(
        path=str(path),
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
        title=str(title) if title else None,
        source_url=source_url,
    )


def check_ingest_limits(media: MediaInfo, settings: Settings) -> None:
    """Enforce configured duration limits on probed media."""
    if media.duration > settings.max_video_minutes * 60:
        raise IngestFailure(
            "too_long",
            f"Media is {media.duration / 60:.1f} minutes long, exceeding the "
            f"{settings.max_video_minutes} minute limit.",
        )
    if not media.has_audio:
        raise IngestFailure("no_audio", "The media file does not contain an audio track.")


def ensure_ffmpeg(settings: Settings) -> None:
    """Fail early with a clear error when FFmpeg tooling is missing."""
    if not shutil.which(settings.ffmpeg_path):
        raise IngestFailure(
            "ffmpeg_missing",
            f"ffmpeg '{settings.ffmpeg_path}' was not found on PATH; "
            "install FFmpeg, then run `openclip doctor`.",
        )
    if not shutil.which(settings.ffprobe_path):
        raise IngestFailure(
            "ffmpeg_missing",
            f"ffprobe '{settings.ffprobe_path}' was not found on PATH; "
            "install FFmpeg, then run `openclip doctor`.",
        )
