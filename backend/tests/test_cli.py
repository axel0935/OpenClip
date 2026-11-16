"""Command-line smoke tests."""

from pathlib import Path

from typer.testing import CliRunner

from openclip.cli.main import app


def test_version() -> None:
    """The package CLI reports its version without starting a server."""
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_ingest_missing_file_exits_one(tmp_path: Path) -> None:
    """Ingest failures print a short message and exit with code 1."""
    result = CliRunner().invoke(
        app, ["ingest", str(tmp_path / "missing.mp4"), "--workdir", str(tmp_path / "w")]
    )
    assert result.exit_code == 1
    assert "error:" in result.stderr
    assert "Traceback" not in result.stderr
