"""Tests for the render stage: filter graphs, encoder choice, clip output."""
# ruff: noqa: E501 - ASS fixture lines are intentionally long

import shutil
import subprocess
from pathlib import Path

import pytest

from openclip.core.config import Settings
from openclip.core.models import Clip, ClipSource, CropKeyframe, CropPath, JobOptions, MediaInfo
from openclip.render import crop_x_expression, video_filter_chain
from openclip.render.filters import preview_filter_chain
from openclip.render.service import detect_encoder, render_clip, render_thumbnail


def _media(path: str = "/tmp/v.mp4", fps: float = 30.0) -> MediaInfo:
    return MediaInfo(path=path, duration=10.0, width=1920, height=1080, fps=fps, has_audio=True)


def _clip() -> Clip:
    return Clip(
        id="c1",
        start=0.0,
        end=4.0,
        score=80.0,
        title="t",
        hook="h",
        hashtags=[],
        rationale="r",
        source=ClipSource.HEURISTIC,
    )


def _crop(layout: str = "fill") -> CropPath:
    return CropPath.model_validate(
        {
            "layout": layout,
            "source_width": 1280,
            "source_height": 720,
            "keyframes": [
                {"t": 0.0, "x": 100, "y": 0, "w": 404, "h": 720},
                {"t": 2.0, "x": 300, "y": 0, "w": 404, "h": 720},
                {"t": 4.0, "x": 500, "y": 0, "w": 404, "h": 720},
            ],
        }
    )


def test_crop_x_expression_shape() -> None:
    expr = crop_x_expression(_crop().keyframes)
    assert expr.startswith("if(lt(t,")
    assert expr.count("if(") == 2
    assert "(100+" in expr or "100+" in expr


def test_crop_x_expression_single_keyframe() -> None:
    kfs = [CropKeyframe(t=0.0, x=42, y=0, w=404, h=720)]
    assert crop_x_expression(kfs) == "42"


def test_crop_x_expression_resamples_long_paths() -> None:
    kfs = [CropKeyframe(t=i * 0.5, x=i * 10 % 500, y=0, w=404, h=720) for i in range(60)]
    expr = crop_x_expression(kfs)
    assert expr.count("if(") <= 19


def test_video_filter_chain_fill() -> None:
    chain = video_filter_chain(_crop("fill"), _media(), "subtitles=filename='a.ass'", 30.0)
    assert "crop=404:720" in chain
    assert "scale=1080:1920" in chain
    assert "subtitles=" in chain
    assert chain.endswith("format=yuv420p")


def test_video_filter_chain_fit() -> None:
    chain = video_filter_chain(_crop("fit"), _media(), None, 24.0)
    assert "split=2" in chain and "overlay" in chain
    assert "fps=24" in chain


def test_video_filter_chain_fps_cap() -> None:
    chain = video_filter_chain(_crop("fit"), _media(fps=24.0), None, 24.0)
    assert "fps=24" in chain


def test_preview_filter_chain() -> None:
    chain = preview_filter_chain(_crop("fill"), _media(), 360, 640)
    assert "scale=360:640" in chain
    assert "subtitles" not in chain


def test_detect_encoder_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENCLIP_ENCODER", "x264")
    settings = Settings()
    settings.data_dir = tmp_path
    assert detect_encoder(settings) == "x264"


def test_detect_encoder_bad_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from openclip.core.errors import ConfigError

    monkeypatch.setenv("OPENCLIP_ENCODER", "vp9")
    settings = Settings()
    settings.data_dir = tmp_path
    with pytest.raises(ConfigError):
        detect_encoder(settings)


def _make_video(path: Path, seconds: float = 3.0) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def _ass_file(path: Path) -> Path:
    path.write_text(
        """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Hello caption
""",
        encoding="utf-8",
    )
    return path


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


@needs_ffmpeg
def test_render_clip_produces_1080x1920(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "src.mp4")
    media = _media(str(video))
    media = MediaInfo(**{**media.model_dump(), "width": 1280, "height": 720})
    ass = _ass_file(tmp_path / "c.ass")
    out = tmp_path / "out" / "clip.mp4"
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    result = render_clip(
        media,
        _clip(),
        _crop("fill"),
        ass,
        out,
        JobOptions(),
        settings=settings,
        encoder="x264",
    )
    assert result == out and out.is_file() and out.stat().st_size > 1000
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "1080,1920" in probe.stdout and "h264" in probe.stdout


@needs_ffmpeg
def test_render_clip_fit_layout(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "src.mp4", seconds=2.0)
    media = MediaInfo(**{**_media(str(video)).model_dump(), "width": 1280, "height": 720})
    clip = Clip(
        id="c1",
        start=0.0,
        end=2.0,
        score=1.0,
        title="t",
        hook="h",
        hashtags=[],
        rationale="r",
        source=ClipSource.HEURISTIC,
    )
    ass = _ass_file(tmp_path / "c.ass")
    out = tmp_path / "fit.mp4"
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    render_clip(
        media, clip, _crop("fit"), ass, out, JobOptions(), settings=settings, encoder="x264"
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
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
        check=True,
    )
    assert "1080,1920" in probe.stdout


@needs_ffmpeg
def test_render_thumbnail(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "src.mp4", seconds=2.0)
    media = MediaInfo(**{**_media(str(video)).model_dump(), "width": 1280, "height": 720})
    out = tmp_path / "thumb.jpg"
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    render_thumbnail(media, _clip(), out, settings)
    assert out.is_file() and out.stat().st_size > 500
