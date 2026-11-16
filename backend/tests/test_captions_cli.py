"""CLI behaviour for ``openclip captions``."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openclip.cli.main import app
from tests.captions_helpers import make_clip, make_transcript


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """Write a transcript and clips file the CLI can consume."""
    transcript = tmp_path / "transcript.json"
    transcript.write_text(make_transcript().model_dump_json(indent=2), encoding="utf-8")
    clips = tmp_path / "clips.json"
    clips.write_text(
        json.dumps(
            [make_clip().model_dump(mode="json"), make_clip(2.0, 4.0).model_dump(mode="json")]
        ),
        encoding="utf-8",
    )
    return transcript, clips


def test_writes_ass_by_default(tmp_path: Path) -> None:
    """The command writes an ASS file for the top-ranked clip."""
    transcript, clips = _write_inputs(tmp_path)
    out = tmp_path / "clip.ass"
    result = CliRunner().invoke(
        app,
        [
            "captions",
            str(transcript),
            "--clips",
            str(clips),
            "--style",
            "clean-pop",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert out.is_file()
    assert out.read_text(encoding="utf-8").startswith("[Script Info]")
    assert "captions:" in result.stdout


def test_selects_clip_by_rank(tmp_path: Path) -> None:
    """``--rank 2`` captions the second clip."""
    transcript, clips = _write_inputs(tmp_path)
    out = tmp_path / "rank2.ass"
    result = CliRunner().invoke(
        app,
        ["captions", str(transcript), "--clips", str(clips), "--rank", "2", "--out", str(out)],
    )
    assert result.exit_code == 0, result.stderr
    assert "clip-1" in result.stdout


def test_selects_clip_by_id(tmp_path: Path) -> None:
    """``--clip-id`` picks the clip with that id."""
    transcript, clips = _write_inputs(tmp_path)
    out = tmp_path / "byid.ass"
    result = CliRunner().invoke(
        app,
        [
            "captions",
            str(transcript),
            "--clips",
            str(clips),
            "--clip-id",
            "clip-1",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stderr


def test_unknown_style_lists_available_ones(tmp_path: Path) -> None:
    """An unknown style is a clean exit 1 naming the valid ids."""
    transcript, clips = _write_inputs(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "captions",
            str(transcript),
            "--clips",
            str(clips),
            "--style",
            "nope",
            "--out",
            str(tmp_path / "a.ass"),
        ],
    )
    assert result.exit_code == 1
    assert "clean-pop" in result.stderr
    assert "Traceback" not in result.stderr


def test_rank_out_of_range_is_rejected(tmp_path: Path) -> None:
    """A rank past the end of the clip list fails with a readable message."""
    transcript, clips = _write_inputs(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "captions",
            str(transcript),
            "--clips",
            str(clips),
            "--rank",
            "9",
            "--out",
            str(tmp_path / "a.ass"),
        ],
    )
    assert result.exit_code == 1
    assert "rank" in result.stderr.lower()


def test_missing_out_is_an_error(tmp_path: Path) -> None:
    """Without ``--out`` or ``--all-styles`` there is nothing to write."""
    transcript, clips = _write_inputs(tmp_path)
    result = CliRunner().invoke(app, ["captions", str(transcript), "--clips", str(clips)])
    assert result.exit_code == 1
    assert "--out" in result.stderr


def test_invalid_transcript_reports_the_file(tmp_path: Path) -> None:
    """Malformed JSON is reported without a traceback."""
    transcript = tmp_path / "bad.json"
    transcript.write_text("{oops", encoding="utf-8")
    clips = tmp_path / "clips.json"
    clips.write_text("[]", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["captions", str(transcript), "--clips", str(clips), "--out", str(tmp_path / "a.ass")]
    )
    assert result.exit_code == 1
    assert "bad.json" in result.stderr
    assert "Traceback" not in result.stderr


def test_empty_clip_list_is_an_error(tmp_path: Path) -> None:
    """An empty clips file asks the user to run analyze first."""
    transcript, _ = _write_inputs(tmp_path)
    clips = tmp_path / "empty.json"
    clips.write_text("[]", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["captions", str(transcript), "--clips", str(clips), "--out", str(tmp_path / "a.ass")]
    )
    assert result.exit_code == 1
    assert "analyze" in result.stderr


def test_preview_requires_video(tmp_path: Path) -> None:
    """``--preview`` without ``--video`` explains what is missing."""
    transcript, clips = _write_inputs(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "captions",
            str(transcript),
            "--clips",
            str(clips),
            "--out",
            str(tmp_path / "a.ass"),
            "--preview",
            str(tmp_path / "p.mp4"),
        ],
    )
    assert result.exit_code == 1
    assert "--video" in result.stderr


def test_all_styles_without_video_writes_only_ass(tmp_path: Path) -> None:
    """``--all-styles`` writes one ASS per preset even without a video."""
    transcript, clips = _write_inputs(tmp_path)
    outdir = tmp_path / "styles"
    result = CliRunner().invoke(
        app, ["captions", str(transcript), "--clips", str(clips), "--all-styles", str(outdir)]
    )
    assert result.exit_code == 0, result.stderr
    written = sorted(path.name for path in outdir.glob("*.ass"))
    assert len(written) == 8
    assert "clean-pop.ass" in written


def test_transcript_cache_envelope_is_accepted(tmp_path: Path) -> None:
    """The transcribe stage writes ``{"transcript": ...}``; the CLI must read it."""
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps({"cache_key": "abc", "transcript": make_transcript().model_dump(mode="json")}),
        encoding="utf-8",
    )
    clips = tmp_path / "clips.json"
    clips.write_text(json.dumps([make_clip().model_dump(mode="json")]), encoding="utf-8")
    out = tmp_path / "cached.ass"
    result = CliRunner().invoke(
        app, ["captions", str(transcript), "--clips", str(clips), "--out", str(out)]
    )
    assert result.exit_code == 0, result.stderr
    assert out.is_file()


@pytest.mark.parametrize("style", ["clean-pop", "bold-impact", "karaoke-glow"])
def test_every_selected_style_builds(tmp_path: Path, style: str) -> None:
    """A few presets end to end through the CLI."""
    transcript, clips = _write_inputs(tmp_path)
    out = tmp_path / f"{style}.ass"
    result = CliRunner().invoke(
        app,
        ["captions", str(transcript), "--clips", str(clips), "--style", style, "--out", str(out)],
    )
    assert result.exit_code == 0, result.stderr
    assert "Dialogue:" in out.read_text(encoding="utf-8")
