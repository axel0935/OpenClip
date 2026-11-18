"""Render stage: ffmpeg MP4 output, thumbnails, proxies and encoder choice."""

import errno
import json
import logging
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from openclip.core.config import Settings
from openclip.core.errors import ConfigError, OpenClipError
from openclip.core.models import Clip, CropPath, JobOptions, MediaInfo
from openclip.render.filters import (
    preview_filter_chain,
    video_filter_chain,
)

log = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str], None]
EncoderName = Literal["x264", "vaapi", "qsv"]

_AUDIO_BITRATE = "128k"
_PREVIEW_SIZE = (360, 640)
_THUMB_SIZE = (540, 960)
_PROGRESS_POLL = 0.25


def _no_op(_fraction: float, _message: str) -> None:
    """Default progress sink."""


def _report(progress: ProgressCallback | None, fraction: float, message: str) -> None:
    (progress or _no_op)(max(0.0, min(1.0, fraction)), message)


class RenderCancelled(OpenClipError):
    """A render stopped on request; carries the shared ``cancelled`` code."""

    code = "cancelled"


class RenderFailure(OpenClipError):
    """A render failed with a machine-readable code (``render_failed``/``disk_full``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RenderCancelled("Rendering cancelled.")


def _is_disk_full(out_path: Path) -> bool:
    """Return True when ``out_path``'s directory cannot take a small write.

    ffmpeg reports only a generic non-zero exit for ``ENOSPC``, so the free
    space is probed after the fact. Any other probe error is reported as
    "not a disk problem" so the original failure is preserved.
    """
    probe = out_path.parent / ".openclip-space-probe"
    try:
        probe.write_bytes(b"0")
    except OSError as exc:
        return exc.errno == errno.ENOSPC
    finally:
        probe.unlink(missing_ok=True)
    return False


def _run_ffmpeg(
    cmd: list[str],
    *,
    settings: Settings,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    total_seconds: float = 0.0,
    progress_range: tuple[float, float] = (0.0, 1.0),
    out_path: Path | None = None,
) -> None:
    """Run ffmpeg with ``-progress pipe:1`` parsing and cancellation.

    ``out_path`` enables the post-failure disk check: a generic non-zero exit
    on a full filesystem becomes a ``disk_full`` error so the UI can show the
    right message instead of "render failed".
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise ConfigError(f"ffmpeg '{settings.ffmpeg_path}' could not be executed.") from exc
    assert proc.stdout is not None
    lo, hi = progress_range
    out_time = 0.0
    try:
        for line in proc.stdout:
            _check_cancel(cancel)
            if line.startswith("out_time_us=") and total_seconds > 0:
                try:
                    out_time = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                frac = min(out_time / total_seconds, 1.0)
                _report(progress, lo + (hi - lo) * frac, "Encoding")
    finally:
        if proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.kill()
            proc.wait(timeout=30)
    if proc.returncode != 0:
        if out_path is not None and _is_disk_full(out_path):
            raise RenderFailure("disk_full", "Not enough space in the data folder for this render.")
        raise RenderFailure("render_failed", f"ffmpeg failed with exit code {proc.returncode}.")


def _encoder_override() -> EncoderName | None:
    import os

    value = os.environ.get("OPENCLIP_ENCODER", "").strip().lower()
    if value in ("x264", "libx264"):
        return "x264"
    if value in ("vaapi", "qsv"):
        return value
    if value in ("", "auto"):
        return None
    raise ConfigError(f"Unknown OPENCLIP_ENCODER '{value}' (expected x264|vaapi|qsv|auto).")


def _hw_encoder_works(settings: Settings, encoder: str) -> bool:
    """Probe whether a hardware H.264 encoder accepts a tiny encode."""
    device = "vaapi" if encoder == "vaapi" else "qsv"
    vf = "format=nv12,hwupload" if encoder == "vaapi" else "format=qsv,hwupload=extra_hw_frames=64"
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=0.5:size=256x256:rate=10",
        "-vf",
        vf,
        "-c:v",
        f"h264_{device}",
        "-f",
        "null",
        "-",
    ]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _measure_encoder(settings: Settings, encoder: str, src: str, seconds: float = 8.0) -> float:
    """Return wall-clock seconds for a tiny benchmark encode; inf on failure."""
    if encoder == "x264":
        tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
    elif encoder == "vaapi":
        tail = ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-global_quality", "25"]
    else:
        tail = [
            "-vf",
            "format=qsv,hwupload=extra_hw_frames=64",
            "-c:v",
            "h264_qsv",
            "-global_quality",
            "25",
        ]
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-t",
        f"{seconds}",
        "-i",
        src,
        *tail,
        "-f",
        "null",
        "-",
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return float("inf")
    return time.monotonic() - started if result.returncode == 0 else float("inf")


_ENCODER_CACHE: dict[str, EncoderName] = {}


def detect_encoder(settings: Settings, media_path: str | None = None) -> EncoderName:
    """Choose the video encoder: env override, else measured auto, else x264."""
    override = _encoder_override()
    if override is not None:
        return override
    cache = settings.data_dir / "encoder.json"
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text()).get("encoder")
        except (ValueError, AttributeError):
            cached = None
        if cached in ("x264", "vaapi", "qsv"):
            return cached  # type: ignore[return-value]
    src = media_path or "testsrc=duration=8:size=640x360:rate=30"
    candidates: list[EncoderName] = ["x264"]
    if _hw_encoder_works(settings, "vaapi"):
        candidates.append("vaapi")
    if _hw_encoder_works(settings, "qsv"):
        candidates.append("qsv")
    timings = {enc: _measure_encoder(settings, enc, src) for enc in candidates}
    best = min(candidates, key=lambda enc: timings[enc])
    choice: EncoderName = best if timings[best] < float("inf") else "x264"
    log.info("encoder auto -> %s (timings %s)", choice, timings)
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"encoder": choice}))
    except OSError:
        pass
    return choice


def _video_args(encoder: EncoderName) -> list[str]:
    """Codec arguments for the chosen encoder."""
    if encoder == "x264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
    if encoder == "vaapi":
        return ["-c:v", "h264_vaapi", "-global_quality", "25"]
    return ["-c:v", "h264_qsv", "-global_quality", "25"]


def _hw_suffix(encoder: EncoderName) -> list[str]:
    """Filter suffix uploading frames to hardware before encoding."""
    if encoder == "x264":
        return []
    if encoder == "vaapi":
        return ["format=nv12,hwupload=extra_hw_frames=64"]
    return ["format=qsv,hwupload=extra_hw_frames=64"]


def render_clip(
    media: MediaInfo,
    clip: Clip,
    crop: CropPath,
    ass_path: Path,
    out_path: Path,
    cfg: JobOptions,
    *,
    settings: Settings | None = None,
    encoder: EncoderName | None = None,
    loudnorm: bool = False,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Render one 1080x1920 H.264/AAC MP4 clip; returns ``out_path``."""
    settings = settings or Settings()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, clip.end - clip.start)
    out_fps = min(media.fps, 30.0)
    encoder = encoder or detect_encoder(settings, media.path)

    from openclip.captions.fonts import ffmpeg_subtitles_filter, fonts_dir_or_none

    ass_filter = ffmpeg_subtitles_filter(ass_path, fonts_dir_or_none())
    chain = video_filter_chain(crop, media, ass_filter, out_fps)
    if encoder != "x264":
        chain += "," + ",".join(_hw_suffix(encoder))

    audio_filters = ["loudnorm"] if loudnorm else []
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{clip.start:.3f}",
        "-i",
        media.path,
        "-t",
        f"{duration:.3f}",
        "-vf",
        chain,
        "-af",
        ",".join(audio_filters) if audio_filters else "anull",
        *_video_args(encoder),
        "-c:a",
        "aac",
        "-b:a",
        _AUDIO_BITRATE,
        "-r",
        f"{out_fps:.6g}",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-f",
        "mp4",
        str(out_path),
    ]
    tmp = out_path.with_suffix(".part.mp4")
    cmd[cmd.index(str(out_path))] = str(tmp)
    _report(progress, 0.02, f"Rendering ({encoder})")
    try:
        _run_ffmpeg(
            cmd,
            settings=settings,
            progress=progress,
            cancel=cancel,
            total_seconds=duration,
            progress_range=(0.02, 0.98),
            out_path=tmp,
        )
    except OpenClipError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(out_path)
    _report(progress, 1.0, "Render complete")
    return out_path


def render_thumbnail(
    media: MediaInfo,
    clip: Clip,
    out_path: Path,
    settings: Settings | None = None,
    *,
    cancel: threading.Event | None = None,
) -> Path:
    """Extract a 540x960 JPEG about 30% into the clip."""
    settings = settings or Settings()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, clip.end - clip.start)
    seek = clip.start + duration * 0.3
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{seek:.3f}",
        "-i",
        media.path,
        "-frames:v",
        "1",
        "-vf",
        f"scale={_THUMB_SIZE[0]}:{_THUMB_SIZE[1]}:force_original_aspect_ratio=increase,"
        f"crop={_THUMB_SIZE[0]}:{_THUMB_SIZE[1]}",
        "-q:v",
        "3",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise OpenClipError(f"Thumbnail extraction failed: {exc}") from exc
    return out_path


def render_preview(
    media: MediaInfo,
    clip: Clip,
    crop: CropPath,
    out_path: Path,
    settings: Settings | None = None,
    *,
    ass_path: Path | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Render a low-res 9:16 proxy for the browser preview.

    When ``ass_path`` is given the same ASS captions as the final render are
    burned in, so the preview matches what the user will download.
    """
    settings = settings or Settings()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, clip.end - clip.start)
    ass_filter = None
    if ass_path is not None:
        from openclip.captions.fonts import ffmpeg_subtitles_filter, fonts_dir_or_none

        ass_filter = ffmpeg_subtitles_filter(ass_path, fonts_dir_or_none())
    chain = preview_filter_chain(crop, media, *_PREVIEW_SIZE, ass_filter=ass_filter)
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{clip.start:.3f}",
        "-i",
        media.path,
        "-t",
        f"{duration:.3f}",
        "-vf",
        chain,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(out_path),
    ]
    try:
        _run_ffmpeg(
            cmd, settings=settings, cancel=cancel, total_seconds=duration, out_path=out_path
        )
    except OpenClipError:
        out_path.unlink(missing_ok=True)
        raise
    return out_path
