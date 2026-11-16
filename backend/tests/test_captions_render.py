"""Integration tests: libass must accept the generated ASS and render pixels.

These tests run real FFmpeg with the ``subtitles`` filter on generated lavfi
footage. They are skipped with an explicit reason when the local FFmpeg is
missing or lacks libass, so a minimal environment still passes.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from openclip.captions.ass import build_ass
from openclip.captions.fonts import fonts_dir
from openclip.captions.presets import load_presets
from openclip.captions.preview import PREVIEW_SECONDS, render_frame, render_preview
from tests.captions_helpers import make_clip, make_transcript


def _ffmpeg() -> str:
    """Return the FFmpeg binary or skip with a clear reason."""
    binary = shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("ffmpeg is not installed, skipping libass integration test")
    return binary


def _has_libass(binary: str) -> bool:
    """Detect libass support without assuming a particular build flag layout."""
    filters = subprocess.run(
        [binary, "-hide_banner", "-filters"], capture_output=True, text=True, check=False
    )
    return " subtitles " in filters.stdout and " ass " in filters.stdout


@pytest.fixture(scope="module")
def ffmpeg() -> str:
    """FFmpeg binary with libass, or skip."""
    binary = _ffmpeg()
    if not _has_libass(binary):
        pytest.skip("ffmpeg lacks the libass 'subtitles' filter, skipping rendering test")
    return binary


@pytest.fixture(scope="module")
def source_video(ffmpeg: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tiny generated video; never committed."""
    path = tmp_path_factory.mktemp("caption-media") / "source.mp4"
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=12:size=960x540:rate=30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"could not generate test video: {result.stderr[-120:]}")
    return path


class TestSubtitlesFilterAcceptsOurAss:
    """The generated document must be valid input for libass."""

    @pytest.mark.parametrize("style_id", sorted(load_presets()))
    def test_ffmpeg_exit_code_zero(
        self, ffmpeg: str, source_video: Path, tmp_path: Path, style_id: str
    ) -> None:
        ass_path = tmp_path / f"{style_id}.ass"
        ass_path.write_text(
            build_ass(make_transcript(), make_clip(), load_presets()[style_id]), encoding="utf-8"
        )
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-t",
                "2",
                "-i",
                str(source_video),
                "-vf",
                (f"scale=1080:1920,subtitles=filename='{ass_path}':fontsdir='{fonts_dir()}'"),
                "-frames:v",
                "15",
                str(tmp_path / f"{style_id}.mp4"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr[-400:]

    def test_captions_actually_draw_pixels(
        self, ffmpeg: str, source_video: Path, tmp_path: Path
    ) -> None:
        """A rendered frame must differ from the same frame without subtitles."""
        style = load_presets()["clean-pop"]
        ass_path = tmp_path / "draw.ass"
        ass_path.write_text(build_ass(make_transcript(), make_clip(), style), encoding="utf-8")
        plain = tmp_path / "plain.png"
        with_hint = tmp_path / "captioned.png"
        base = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "0.2",
            "-i",
            str(source_video),
            "-frames:v",
            "1",
            "-update",
            "1",
        ]
        subprocess.run([*base, "-vf", "scale=1080:1920", str(plain)], check=True)
        subprocess.run(
            [
                *base,
                "-vf",
                (f"scale=1080:1920,subtitles=filename='{ass_path}':fontsdir='{fonts_dir()}'"),
                str(with_hint),
            ],
            check=True,
        )
        assert plain.read_bytes() != with_hint.read_bytes()


class TestPreviewHelpers:
    """Preview rendering must produce playable, correctly sized output."""

    def test_render_preview(self, ffmpeg: str, source_video: Path, tmp_path: Path) -> None:
        clip = make_clip(start=0.0, end=10.0)
        ass_path = tmp_path / "clip.ass"
        ass_path.write_text(
            build_ass(make_transcript(), clip, load_presets()["clean-pop"]), encoding="utf-8"
        )
        out = render_preview(
            source_video,
            clip,
            ass_path,
            tmp_path / "preview.mp4",
            seconds=PREVIEW_SECONDS,
            start_offset=0.5,
            ffmpeg=ffmpeg,
        )
        assert out.is_file() and out.stat().st_size > 0
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            assert probe.stdout.strip() == "1080,1920"

    def test_render_frame_with_flat_background(
        self, ffmpeg: str, source_video: Path, tmp_path: Path
    ) -> None:
        clip = make_clip(start=0.0, end=10.0)
        ass_path = tmp_path / "frame.ass"
        ass_path.write_text(
            build_ass(make_transcript(), clip, load_presets()["clean-pop"]), encoding="utf-8"
        )
        out = render_frame(
            source_video, clip, ass_path, tmp_path / "frame.png", at=1.0, ffmpeg=ffmpeg
        )
        assert out.is_file() and out.stat().st_size > 0

    def test_missing_source_raises(self, ffmpeg: str, tmp_path: Path) -> None:
        from openclip.core.errors import RenderError

        ass_path = tmp_path / "a.ass"
        ass_path.write_text(
            build_ass(make_transcript(), make_clip(), load_presets()["clean-pop"]), encoding="utf-8"
        )
        with pytest.raises(RenderError):
            render_preview(
                tmp_path / "missing.mp4",
                make_clip(),
                ass_path,
                tmp_path / "out.mp4",
                ffmpeg=ffmpeg,
            )
