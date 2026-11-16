"""Implementation of ``openclip captions``."""

import json
import logging
from pathlib import Path

import typer

from openclip.captions.ass import build_ass
from openclip.captions.presets import load_presets
from openclip.captions.preview import (
    PREVIEW_SECONDS,
    SHEET_BACKGROUND,
    contact_sheet,
    render_frame,
    render_preview,
    sample_time,
)
from openclip.core.config import Settings
from openclip.core.errors import OpenClipError
from openclip.core.models import CaptionStyle, Clip, Transcript

LOGGER = logging.getLogger(__name__)


def load_transcript(path: Path) -> Transcript:
    """Load a transcript from a plain ``Transcript`` JSON or a cache envelope."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OpenClipError(f"Cannot read transcript {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OpenClipError(f"Transcript {path.name} is not valid JSON: {exc}") from exc
    try:
        if isinstance(data, dict) and "transcript" in data:
            return Transcript.model_validate(data["transcript"])
        return Transcript.model_validate(data)
    except ValueError as exc:
        raise OpenClipError(f"Transcript {path.name} is invalid: {exc}") from exc


def load_clips(path: Path) -> list[Clip]:
    """Load a clip list from ``clips.json``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OpenClipError(f"Cannot read clips {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OpenClipError(f"Clips file {path.name} is not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("clips", [])
    if not isinstance(data, list):
        raise OpenClipError(f"Clips file {path.name} must contain a JSON list of clips.")
    try:
        return [Clip.model_validate(item) for item in data]
    except ValueError as exc:
        raise OpenClipError(f"Clips file {path.name} is invalid: {exc}") from exc


def select_clip(clips: list[Clip], rank: int | None, clip_id: str | None) -> Clip:
    """Pick one clip by 1-based rank or by id, defaulting to rank 1."""
    if not clips:
        raise OpenClipError("The clips file is empty; run 'openclip analyze' first.")
    if clip_id is not None:
        for clip in clips:
            if clip.id == clip_id:
                return clip
        raise OpenClipError(f"No clip with id {clip_id!r} in the clips file.")
    index = (rank or 1) - 1
    if index < 0 or index >= len(clips):
        raise OpenClipError(f"--rank must be between 1 and {len(clips)}.")
    return clips[index]


def resolve_style(styles: dict[str, CaptionStyle], style_id: str) -> CaptionStyle:
    """Look up one preset by id with a helpful error message."""
    style = styles.get(style_id)
    if style is None:
        available = ", ".join(sorted(styles)) or "none"
        raise OpenClipError(f"Unknown caption style {style_id!r}. Available: {available}.")
    return style


def _echo(message: str) -> None:
    """Print one line of human-readable output."""
    typer.echo(message)


def _build_clip_ass(transcript: Transcript, clip: Clip, style: CaptionStyle, out: Path) -> Path:
    """Build one ASS file and write it, returning its path."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_ass(transcript, clip, style), encoding="utf-8")
    return out


def run_captions(
    transcript_path: Path,
    clips_path: Path,
    *,
    style_id: str,
    out: Path | None,
    rank: int | None,
    clip_id: str | None,
    preview: Path | None,
    video: Path | None,
    all_styles: Path | None,
    presets_dir: Path | None = None,
    settings: Settings | None = None,
) -> None:
    """Build ASS subtitles, and optionally previews or a style sheet."""
    settings = settings or Settings()
    transcript = load_transcript(transcript_path)
    clips = load_clips(clips_path)
    styles = load_presets(presets_dir)

    if all_styles is not None:
        _render_all_styles(
            transcript,
            clips,
            styles,
            all_styles,
            video=video,
            presets_dir=presets_dir,
            settings=settings,
        )
        return

    if out is None:
        raise OpenClipError("Provide --out for the ASS file (or use --all-styles).")
    clip = select_clip(clips, rank, clip_id)
    style = resolve_style(styles, style_id)
    ass_path = _build_clip_ass(transcript, clip, style, out)
    _echo(f"clip: {clip.id} ({clip.start:.2f}s-{clip.end:.2f}s)")
    _echo(f"style: {style.id}")
    _echo(f"captions: {ass_path}")

    if preview is not None:
        if video is None:
            raise OpenClipError("--preview needs --video pointing at the source video.")
        render_preview(video, clip, ass_path, preview, ffmpeg=settings.ffmpeg_path)
        _echo(f"preview: {preview}")


def _render_all_styles(
    transcript: Transcript,
    clips: list[Clip],
    styles: dict[str, CaptionStyle],
    outdir: Path,
    *,
    video: Path | None,
    presets_dir: Path | None,
    settings: Settings,
) -> None:
    """Write ASS, a short preview and a PNG frame for every preset plus a sheet."""
    del presets_dir
    clip = select_clip(clips, None, None)
    outdir.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[str, Path]] = []
    for style_id, style in styles.items():
        ass_path = _build_clip_ass(transcript, clip, style, outdir / f"{style_id}.ass")
        _echo(f"captions: {ass_path}")
        if video is None:
            continue
        at = sample_time(transcript, clip, style)
        render_preview(
            video,
            clip,
            ass_path,
            outdir / f"{style_id}.mp4",
            seconds=PREVIEW_SECONDS,
            start_offset=at,
            ffmpeg=settings.ffmpeg_path,
        )
        frame = render_frame(
            video,
            clip,
            ass_path,
            outdir / f"{style_id}.png",
            at=at,
            ffmpeg=settings.ffmpeg_path,
            background=SHEET_BACKGROUND,
        )
        entries.append((style.name, frame))
        _echo(f"preview: {outdir / f'{style_id}.mp4'}")
    if entries:
        sheet = contact_sheet(entries, outdir / "styles_sheet.png")
        _echo(f"sheet: {sheet}")
