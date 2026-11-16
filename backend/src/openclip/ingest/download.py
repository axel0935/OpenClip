"""yt-dlp based remote video download with progress and size limits."""

import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openclip.core.config import Settings
from openclip.ingest.errors import IngestFailure
from openclip.ingest.urlcheck import validate_download_url

ProgressCallback = Callable[[float, str], None]

_MAX_HEIGHT = 1080
_FORMAT_SELECTOR = "bestvideo[height<=1080]+bestaudio/bestvideo+bestaudio/best[height<=1080]/best"


def _no_op_progress(_fraction: float, _message: str) -> None:
    """Default progress sink."""


def download_video(
    url: str,
    workdir: Path,
    settings: Settings,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download a single video to ``workdir`` and return the media file path."""
    validate_download_url(url)
    if shutil.which("yt-dlp") is None:
        raise IngestFailure("download_failed", "yt-dlp was not found; cannot download URLs.")
    report = progress or _no_op_progress
    max_bytes = settings.max_download_mb * 1024 * 1024
    state = {"downloaded": 0, "total": 0}

    def _hook(info: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise IngestFailure("cancelled", "Download cancelled.")
        status = info.get("status")
        if status == "downloading":
            downloaded = int(info.get("downloaded_bytes") or 0)
            total = int(info.get("total_bytes") or info.get("total_bytes_estimate") or 0)
            state["downloaded"] = downloaded
            if total:
                state["total"] = total
                report(min(downloaded / total, 0.95), "Downloading video")
            elif downloaded > max_bytes:
                raise IngestFailure(
                    "too_large",
                    f"Download exceeded the {settings.max_download_mb} MB limit.",
                )
        elif status == "finished":
            report(0.97, "Download finished, merging formats")

    try:
        from yt_dlp import YoutubeDL  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise IngestFailure(
            "download_failed", "yt-dlp is not installed; cannot download URLs."
        ) from exc

    ydl_opts: Any = {
        "format": _FORMAT_SELECTOR,
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "media.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "max_filesize": max_bytes,
        "socket_timeout": 30,
        "retries": 3,
        "progress_hooks": [_hook],
        "restrictfilenames": False,
    }
    report(0.02, "Fetching video metadata")
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if isinstance(info, dict) and info.get("entries"):
                raise IngestFailure(
                    "invalid_url", "Playlists are not supported; use a single video URL."
                )
            duration = (info or {}).get("duration")
            if isinstance(duration, (int, float)) and duration > settings.max_video_minutes * 60:
                raise IngestFailure(
                    "too_long",
                    f"Video is {duration / 60:.1f} minutes long, exceeding the "
                    f"{settings.max_video_minutes} minute limit.",
                )
            ydl.download([url])
    except IngestFailure:
        raise
    except Exception as exc:
        raise IngestFailure("download_failed", f"Download failed: {exc}") from exc

    preferred = workdir / "media.mp4"
    if preferred.is_file() and preferred.stat().st_size > 0:
        media = preferred
    else:
        candidates = [
            p for p in workdir.glob("media.*") if p.is_file() and p.suffix not in {".part", ".ytdl"}
        ]
        media = max(candidates, key=lambda p: p.stat().st_size, default=None)
    if media is None or media.stat().st_size == 0:
        raise IngestFailure("download_failed", "Download finished but no media file was produced.")
    if media.suffix.lower() != ".mp4":
        remuxed = workdir / "media.mp4"
        remux = subprocess.run(
            [
                settings.ffmpeg_path,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(media),
                "-c",
                "copy",
                str(remuxed),
            ],
            capture_output=True,
            check=False,
        )
        if remux.returncode == 0 and remuxed.is_file():
            media = remuxed
    if media.stat().st_size > max_bytes:
        raise IngestFailure(
            "too_large", f"Downloaded file exceeds the {settings.max_download_mb} MB limit."
        )
    return media
