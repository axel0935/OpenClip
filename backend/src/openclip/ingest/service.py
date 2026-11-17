"""Media acquisition: local files and validated remote URLs."""

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from openclip.core.config import Settings
from openclip.core.models import JobSource, MediaInfo
from openclip.ingest.download import download_video
from openclip.ingest.errors import IngestFailure
from openclip.ingest.ffprobe import (
    check_ingest_limits,
    ensure_ffmpeg,
    probe_media,
)

ProgressCallback = Callable[[float, str], None]

_MEDIA_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".opus",
}


def _no_op_progress(_fraction: float, _message: str) -> None:
    """Default progress sink."""


def _report(progress: ProgressCallback | None, fraction: float, message: str) -> None:
    (progress or _no_op_progress)(max(0.0, min(1.0, fraction)), message)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise IngestFailure("cancelled", "Operation cancelled.")


def _copy_local(
    source: Path,
    workdir: Path,
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> Path:
    """Copy a local media file into the work directory, streaming chunks."""
    suffix = source.suffix.lower() or ".bin"
    dest = workdir / f"media{suffix}"
    total = source.stat().st_size
    copied = 0
    _report(progress, 0.0, "Copying media into work directory")
    with source.open("rb") as src, dest.open("wb") as dst:
        while True:
            _check_cancel(cancel)
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            copied += len(chunk)
            if total:
                _report(progress, 0.35 * copied / total, "Copying media into work directory")
    return dest


def extract_audio(
    media_path: Path,
    workdir: Path,
    settings: Settings,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    duration: float = 0.0,
) -> Path:
    """Extract a mono 16 kHz WAV next to the media file using FFmpeg."""
    ensure_ffmpeg(settings)
    out_path = workdir / "audio.wav"
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(out_path),
    ]
    _report(progress, 0.9, "Extracting audio track")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise IngestFailure(
            "ffmpeg_missing",
            f"ffmpeg '{settings.ffmpeg_path}' could not be executed; run `openclip doctor`.",
        ) from exc
    assert proc.stdout is not None
    for line in proc.stdout:
        _check_cancel(cancel)
        if line.startswith("out_time_us=") and duration > 0:
            try:
                out_us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            fraction = min(out_us / 1_000_000 / duration, 1.0)
            _report(progress, 0.9 + 0.09 * fraction, "Extracting audio track")
    returncode = proc.wait()
    if returncode != 0:
        raise IngestFailure(
            "unsupported_file", f"ffmpeg failed to extract audio from '{media_path}'."
        )
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise IngestFailure("unsupported_file", "Audio extraction produced an empty file.")
    return out_path


def _resolve_source(source: JobSource | str | Path) -> tuple[str, str]:
    """Normalize a source argument into ``("url" | "file", value)``."""
    if isinstance(source, JobSource):
        return source.type, source.value
    text = str(source)
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return "url", text
    if "://" in text:
        scheme = text.split("://", 1)[0]
        raise IngestFailure("invalid_url", f"Unsupported URL scheme '{scheme}'.")
    return "file", text


def ingest(
    source: JobSource | str | Path,
    workdir: Path,
    settings: Settings | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> MediaInfo:
    """Acquire ``source`` into ``workdir`` and return its :class:`MediaInfo`.

    ``source`` may be a local path, an ``http(s)`` URL, or a
    :class:`JobSource`. Local files are copied into ``workdir``; URLs are
    downloaded with yt-dlp after SSRF validation. A mono 16 kHz ``audio.wav``
    is always extracted for downstream stages.
    """
    cfg = settings or Settings()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    ensure_ffmpeg(cfg)
    kind, value = _resolve_source(source)

    if kind == "url":
        _report(progress, 0.0, "Validating URL")
        media_path = download_video(value, workdir, cfg, progress, cancel)
        _report(progress, 0.4, "Probing downloaded media")
        media = probe_media(media_path, cfg, source_url=value)
    else:
        path = Path(value).expanduser()
        if not path.exists():
            raise IngestFailure("not_found", f"File not found: {value}")
        if not path.is_file():
            raise IngestFailure("unsupported_file", f"Not a regular file: {value}")
        if path.suffix.lower() not in _MEDIA_EXTENSIONS:
            raise IngestFailure(
                "unsupported_file",
                f"Unsupported media extension '{path.suffix}'; expected one of "
                f"{', '.join(sorted(_MEDIA_EXTENSIONS))}.",
            )
        media_path = _copy_local(path, workdir, progress, cancel)
        _report(progress, 0.5, "Probing media")
        media = probe_media(media_path, cfg)

    _check_cancel(cancel)
    check_ingest_limits(media, cfg)
    extract_audio(media_path, workdir, cfg, progress, cancel, duration=media.duration)
    _report(progress, 1.0, "Ingest complete")
    (workdir / "media.json").write_text(media.model_dump_json(indent=2), encoding="utf-8")
    return media
