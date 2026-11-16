"""Typer command-line entry point for OpenClip."""

from typing import Annotated

import typer
import uvicorn

from openclip import __version__
from openclip.api.app import create_app
from openclip.core.config import Settings

app = typer.Typer(help="Local-first long-video clipper.", no_args_is_help=True)


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


def _not_implemented() -> None:
    """Exit consistently for future pipeline commands."""
    typer.echo("not implemented yet")
    raise typer.Exit(code=2)


@app.command()
def ingest() -> None:
    """Run the future ingest stage."""
    _not_implemented()


@app.command()
def transcribe() -> None:
    """Run the future transcription stage."""
    _not_implemented()


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
