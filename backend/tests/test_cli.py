"""Command-line smoke tests."""

from typer.testing import CliRunner

from openclip.cli.main import app


def test_version() -> None:
    """The package CLI reports its version without starting a server."""
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"
