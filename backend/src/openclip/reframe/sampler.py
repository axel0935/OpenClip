"""Memory-safe frame sampling from FFmpeg for the clip time range."""

import logging
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np  # pyright: ignore[reportMissingImports] - vision extra

from openclip.core.config import Settings
from openclip.core.models import MediaInfo
from openclip.reframe.errors import ReframeError

log = logging.getLogger(__name__)

_SAMPLE_FPS = 4
_SAMPLE_WIDTH = 640
_PROBE_TIMEOUT = 30

ProgressCallback = Callable[[float, str], None]


def _no_op(_fraction: float, _message: str) -> None:
    """Default progress sink."""


def _report(progress: ProgressCallback | None, fraction: float, message: str) -> None:
    (progress or _no_op)(max(0.0, min(1.0, fraction)), message)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ReframeError("cancelled", "Reframing cancelled.")


@dataclass(frozen=True)
class SampledFrame:
    """One downscaled BGR frame with its clip-relative timestamp."""

    t: float
    image: np.ndarray


def scaled_frame_size(media: MediaInfo) -> tuple[int, int]:
    """Return ``(width, height)`` of the ffmpeg ``scale=640:-2`` output."""
    height = int(media.height * _SAMPLE_WIDTH / media.width)
    height -= height % 2
    return _SAMPLE_WIDTH, max(2, height)


def iter_clip_frames(
    media: MediaInfo,
    clip_start: float,
    clip_end: float,
    settings: Settings,
    *,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Iterator[SampledFrame]:
    """Stream the clip's frames at ~4 fps, one frame in memory at a time.

    Raises :class:`ReframeError` with code ``no_video_stream`` when ffmpeg
    cannot decode the range.
    """
    duration = max(0.1, clip_end - clip_start)
    width, height = scaled_frame_size(media)
    frame_bytes = width * height * 3
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{clip_start:.3f}",
        "-i",
        media.path,
        "-t",
        f"{duration:.3f}",
        "-vf",
        f"fps={_SAMPLE_FPS},scale={_SAMPLE_WIDTH}:-2",
        "-an",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-",
    ]
    _report(progress, 0.05, "Sampling clip frames")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=frame_bytes * 4,
        )
    except FileNotFoundError as exc:
        raise ReframeError(
            "ffmpeg_missing", f"ffmpeg '{settings.ffmpeg_path}' could not be executed."
        ) from exc
    assert proc.stdout is not None
    expected = max(1.0, duration * _SAMPLE_FPS)
    index = 0
    try:
        while True:
            _check_cancel(cancel)
            chunk = proc.stdout.read(frame_bytes)
            if len(chunk) < frame_bytes:
                break
            image = np.frombuffer(chunk, dtype=np.uint8).reshape(height, width, 3)
            yield SampledFrame(t=index / _SAMPLE_FPS, image=image.copy())
            index += 1
            if index % 8 == 0:
                _report(progress, 0.05 + 0.55 * min(index / expected, 1.0), "Sampling clip frames")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    if index == 0:
        raise ReframeError(
            "no_video_stream",
            f"Could not decode video frames from '{media.path}' for the clip range.",
        )
    _report(progress, 0.6, f"Sampled {index} frames")
