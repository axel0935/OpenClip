"""``openclip reframe`` command: compute a CropPath for one clip."""

import json
import time
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer

from openclip.core.config import Settings
from openclip.core.errors import OpenClipError
from openclip.core.models import Clip, MediaInfo
from openclip.ingest.ffprobe import probe_media
from openclip.reframe import compute_crop_path, render_debug_video


def _fail(exc: OpenClipError) -> NoReturn:
    """Report an expected failure as a short message and exit code 1."""
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


def _load_media(value: str, settings: Settings) -> MediaInfo:
    """Resolve a media argument into MediaInfo from JSON or a video path."""
    media_path = Path(value)
    if media_path.suffix.lower() == ".json":
        return MediaInfo.model_validate_json(media_path.read_text(encoding="utf-8"))
    return probe_media(media_path, settings)


def _load_clips(path: Path) -> list[Clip]:
    """Load the Clip list written by ``openclip analyze``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise OpenClipError("clips file must contain a JSON list of clips.")
    return [Clip.model_validate(item) for item in data]


def _pick_clip(clips: list[Clip], rank: int | None, clip_id: str | None) -> Clip:
    """Select a clip by 1-based rank or by id."""
    if not clips:
        raise OpenClipError("clips file contains no clips.")
    if clip_id is not None:
        for clip in clips:
            if clip.id == clip_id:
                return clip
        raise OpenClipError(f"No clip with id '{clip_id}'.")
    index = (rank or 1) - 1
    if index < 0 or index >= len(clips):
        raise OpenClipError(f"Rank {rank or 1} is out of range (1-{len(clips)}).")
    return clips[index]


def _progress_bar(label: str):
    """Return a ``(fraction, message)`` callback backed by a typer progress bar."""
    bar = typer.progressbar(length=1000, label=label)
    progress = bar.__enter__()
    state = {"value": 0}

    def report(fraction: float, message: str) -> None:
        target = max(state["value"], min(1000, int(fraction * 1000)))
        if target > state["value"]:
            progress.update(target - state["value"])
            state["value"] = target
        if message:
            progress.label = message

    def close() -> None:
        bar.__exit__(None, None, None)

    return report, close


def reframe(
    media: Annotated[str, typer.Argument(help="MediaInfo JSON file or video path.")],
    clips: Annotated[Path, typer.Option("--clips", help="clips.json from analyze.")],
    rank: Annotated[int | None, typer.Option("--rank", help="1-based clip rank.", min=1)] = None,
    clip_id: Annotated[
        str | None, typer.Option("--clip-id", help="Clip id instead of rank.")
    ] = None,
    layout: Annotated[
        Literal["auto", "fill", "fit"],
        typer.Option("--layout", help="Crop layout."),
    ] = "auto",
    out: Annotated[Path, typer.Option("--out", help="Output CropPath JSON path.")] = Path(
        "crop.json"
    ),
    debug_video: Annotated[
        Path | None,
        typer.Option("--debug-video", help="Write a short preview with the crop drawn."),
    ] = None,
) -> None:
    """Compute the vertical crop path for one clip and write crop.json."""
    settings = Settings()
    try:
        media_info = _load_media(media, settings)
        clip = _pick_clip(_load_clips(clips), rank, clip_id)
        report, close = _progress_bar("Reframing")
        started = time.monotonic()
        try:
            crop = compute_crop_path(media_info, clip, layout, settings=settings, progress=report)
            if debug_video is not None:
                render_debug_video(
                    media_info,
                    crop,
                    clip.start,
                    clip.end,
                    debug_video,
                    settings,
                    progress=report,
                )
        finally:
            close()
        elapsed = time.monotonic() - started
    except OpenClipError as exc:
        _fail(exc)
    except (OSError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    out.write_text(crop.model_dump_json(indent=2), encoding="utf-8")
    duration = clip.end - clip.start
    typer.echo(f"clip: {clip.id} ({clip.start:.1f}s-{clip.end:.1f}s)")
    typer.echo(f"layout: {crop.layout}")
    typer.echo(f"keyframes: {len(crop.keyframes)}")
    typer.echo(f"elapsed: {elapsed:.2f}s ({elapsed / max(duration, 0.1):.2f}x realtime)")
    typer.echo(f"crop: {out}")
    if debug_video is not None:
        typer.echo(f"debug video: {debug_video}")


__all__ = ["reframe"]
