"""Typer command-line entry point for OpenClip."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer
import uvicorn

from openclip import __version__
from openclip.api.app import create_app
from openclip.core.config import Settings
from openclip.core.errors import OpenClipError
from openclip.core.models import JobSource, MediaInfo
from openclip.ingest import ingest as run_ingest
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


def _not_implemented() -> None:
    """Exit consistently for future pipeline commands."""
    typer.echo("not implemented yet")
    raise typer.Exit(code=2)


@app.command()
def analyze() -> None:
    """Run the future analysis stage."""
    _not_implemented()


@app.command()
def captions() -> None:
    """Run the future captions stage."""
    _not_implemented()


@app.command()
def run() -> None:
    """Run the future complete pipeline."""
    _not_implemented()
