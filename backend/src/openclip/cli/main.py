"""Typer command-line entry point for OpenClip."""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer
import uvicorn

from openclip import __version__
from openclip.analyze import analyze as run_analyze
from openclip.api.app import create_app
from openclip.core.config import Settings
from openclip.core.errors import OpenClipError
from openclip.core.models import JobOptions, JobSource, MediaInfo, Transcript
from openclip.ingest import ingest as run_ingest
from openclip.ingest.ffprobe import probe_media
from openclip.transcribe import TranscribeConfig
from openclip.transcribe import transcribe as run_transcribe

app = typer.Typer(help="Local-first long-video clipper.", no_args_is_help=True)

ProgressFn = Callable[[float, str], None]


def _version_callback(value: bool) -> None:
    """Print the installed application version for an eager option."""
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = None,
) -> None:
    """Run OpenClip commands."""


@app.command()
def serve() -> None:
    """Run the local API server on the configured host and port."""
    settings = Settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


def _fail(exc: OpenClipError) -> NoReturn:
    """Report an expected failure as a short message and exit code 1."""
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


def _source_arg(value: str) -> JobSource:
    """Build a :class:`JobSource` from a path-or-URL CLI argument."""
    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        return JobSource(type="url", value=value)
    return JobSource(type="upload", value=value)


def _progress_bar(label: str) -> tuple[ProgressFn, Callable[[], None]]:
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


def _load_or_ingest(source: str, workdir: Path, settings: Settings) -> tuple[MediaInfo, bool]:
    """Reuse ``workdir/media.json`` when present, otherwise run ingest."""
    media_file = workdir / "media.json"
    if media_file.is_file():
        try:
            return MediaInfo.model_validate_json(media_file.read_text(encoding="utf-8")), False
        except ValueError:
            pass
    report, close = _progress_bar("Ingesting media")
    try:
        return run_ingest(_source_arg(source), workdir, settings=settings, progress=report), True
    finally:
        close()


@app.command()
def ingest(
    source: Annotated[str, typer.Argument(help="Local media path or http(s) URL.")],
    workdir: Annotated[Path, typer.Option("--workdir", help="Job work directory.")],
) -> None:
    """Acquire a media source and print its MediaInfo as JSON."""
    settings = Settings()
    try:
        media, _ = _load_or_ingest(source, workdir, settings)
    except OpenClipError as exc:
        _fail(exc)
    typer.echo(media.model_dump_json(indent=2))


@app.command()
def transcribe(
    source: Annotated[str, typer.Argument(help="Local media path or http(s) URL.")],
    workdir: Annotated[Path, typer.Option("--workdir", help="Job work directory.")],
    profile: Annotated[
        Literal["auto", "fast", "balanced", "quality"],
        typer.Option("--profile", help="Whisper transcription profile."),
    ] = "auto",
) -> None:
    """Transcribe media into word-level timestamps and cache transcript.json."""
    settings = Settings()
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        media, _ = _load_or_ingest(source, workdir, settings)
        report, close = _progress_bar("Transcribing")
        started = time.monotonic()
        try:
            transcript = run_transcribe(
                media,
                TranscribeConfig(profile=profile),
                workdir=workdir,
                settings=settings,
                progress=report,
            )
        finally:
            close()
        elapsed = time.monotonic() - started
    except OpenClipError as exc:
        _fail(exc)

    word_count = sum(len(segment.words) for segment in transcript.segments)
    rtf = elapsed / transcript.duration if transcript.duration > 0 else 0.0
    typer.echo(f"language: {transcript.language}")
    typer.echo(f"duration: {transcript.duration:.2f}s")
    typer.echo(f"segments: {len(transcript.segments)}")
    typer.echo(f"words: {word_count}")
    typer.echo(f"model: {transcript.model}")
    typer.echo(f"elapsed: {elapsed:.2f}s")
    typer.echo(f"realtime factor: {rtf:.2f}x")
    typer.echo(f"transcript: {workdir / 'transcript.json'}")


def _load_transcript(path: Path) -> Transcript:
    """Load a Transcript from a raw JSON file or a transcript.json cache envelope."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "transcript" in data:
        return Transcript.model_validate(data["transcript"])
    return Transcript.model_validate(data)


def _load_media(value: str, workdir: Path, settings: Settings) -> MediaInfo:
    """Resolve --media into MediaInfo from a JSON file or a video path."""
    media_path = Path(value)
    if media_path.suffix.lower() == ".json":
        return MediaInfo.model_validate_json(media_path.read_text(encoding="utf-8"))
    return probe_media(media_path, settings)


def _fmt_time(seconds: float) -> str:
    """Format seconds as m:ss for the CLI table."""
    minutes, secs = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes)}:{secs:04.1f}"


@app.command()
def analyze(
    transcript_path: Annotated[Path, typer.Argument(help="Path to transcript.json.")],
    media: Annotated[
        str | None,
        typer.Option("--media", help="MediaInfo JSON file or video path."),
    ] = None,
    workdir: Annotated[
        Path | None,
        typer.Option("--workdir", help="Directory for clips.json (default: transcript dir)."),
    ] = None,
    mode: Annotated[
        Literal["auto", "heuristic", "llm"],
        typer.Option("--mode", help="Analysis mode."),
    ] = "auto",
    max_clips: Annotated[
        int | None,
        typer.Option("--max-clips", help="Maximum number of clips.", min=1),
    ] = None,
) -> None:
    """Detect highlight clips from a transcript and print a ranked table."""
    settings = Settings()
    out_dir = workdir or transcript_path.parent
    try:
        transcript = _load_transcript(transcript_path)
        if media is not None:
            media_info = _load_media(media, out_dir, settings)
        else:
            media_file = out_dir / "media.json"
            if not media_file.is_file():
                raise OpenClipError("Provide --media or a media.json next to the transcript.")
            media_info = MediaInfo.model_validate_json(media_file.read_text(encoding="utf-8"))
        options = JobOptions(analysis_mode=mode, max_clips=max_clips or 8)
        report, close = _progress_bar("Analyzing")
        try:
            clips = run_analyze(transcript, media_info, options, settings=settings, progress=report)
        finally:
            close()
    except OpenClipError as exc:
        _fail(exc)
    except (OSError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not clips:
        typer.echo("No clips found.")
        return
    typer.echo(f"{'#':<3} {'start-end':<13} {'dur':>5} {'score':>6}  title")
    for rank, clip in enumerate(clips, 1):
        span = f"{_fmt_time(clip.start)}-{_fmt_time(clip.end)}"
        typer.echo(
            f"{rank:<3} {span:<13} {clip.end - clip.start:>4.0f}s {clip.score:>6.1f}  {clip.title}"
        )
    out_path = out_dir / "clips.json"
    out_path.write_text(
        json.dumps([c.model_dump(mode="json") for c in clips], indent=2), encoding="utf-8"
    )
    typer.echo(f"clips: {out_path}")


def _not_implemented() -> None:
    """Exit consistently for future pipeline commands."""
    typer.echo("not implemented yet")
    raise typer.Exit(code=2)


@app.command()
def captions(
    transcript: Annotated[Path, typer.Argument(help="Path to transcript.json.")],
    clips: Annotated[Path, typer.Option("--clips", help="Path to clips.json.")],
    out: Annotated[
        Path | None, typer.Option("--out", help="Output .ass file for the selected clip.")
    ] = None,
    style: Annotated[
        str, typer.Option("--style", help="Caption style id from presets/captions.")
    ] = "clean-pop",
    rank: Annotated[
        int | None, typer.Option("--rank", help="Select the clip by 1-based rank.", min=1)
    ] = None,
    clip_id: Annotated[str | None, typer.Option("--clip-id", help="Select the clip by id.")] = None,
    preview: Annotated[
        Path | None, typer.Option("--preview", help="Render a short preview MP4 here.")
    ] = None,
    video: Annotated[
        Path | None, typer.Option("--video", help="Source video for --preview / --all-styles.")
    ] = None,
    all_styles: Annotated[
        Path | None,
        typer.Option(
            "--all-styles", help="Write every preset's ASS, preview and a PNG sheet here."
        ),
    ] = None,
    presets_dir: Annotated[
        Path | None, typer.Option("--presets-dir", help="Directory of caption preset JSON files.")
    ] = None,
) -> None:
    """Build ASS captions for one clip, or previews for every style."""
    from openclip.cli.captions_cmd import run_captions

    try:
        run_captions(
            transcript,
            clips,
            style_id=style,
            out=out,
            rank=rank,
            clip_id=clip_id,
            preview=preview,
            video=video,
            all_styles=all_styles,
            presets_dir=presets_dir,
        )
    except OpenClipError as exc:
        _fail(exc)


@app.command()
def run() -> None:
    """Run the future complete pipeline."""
    _not_implemented()


# Step 6: vertical reframing. Registered here because cli/main.py was clean.
# The reframe stage needs the optional 'vision' extra (numpy/opencv); without
# it the command stays registered but reports a clear error.
try:
    from openclip.cli.reframe_cmd import reframe as _reframe_impl  # noqa: E402
except ImportError:  # pragma: no cover - exercised only without the vision extra
    _reframe_impl = None

if _reframe_impl is not None:
    app.command(name="reframe")(_reframe_impl)
else:

    @app.command(name="reframe")
    def _reframe_missing() -> None:
        typer.echo(
            "error: reframing requires the 'vision' extra (run: uv sync --extra vision)",
            err=True,
        )
        raise typer.Exit(code=1)
