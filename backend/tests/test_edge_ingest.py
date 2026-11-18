"""Edge-case matrix: ingest, probing and media-shape robustness.

Every case here uses tiny generated fixtures in a pytest ``tmp_path`` (never a
committed media file). Cases that need a real video are skipped when FFmpeg is
unavailable; the pure-logic ones always run.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openclip.core.config import Settings
from openclip.ingest import ingest
from openclip.ingest.errors import IngestFailure
from openclip.ingest.ffprobe import check_ingest_limits, probe_media
from tests.media import make_corrupt_media, make_video_no_audio, make_video_with_audio


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    return settings


def _ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


# ---------- broken containers ----------


def test_zero_byte_file_is_rejected(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    source = tmp_path / "empty.mp4"
    source.write_bytes(b"")
    with pytest.raises(IngestFailure) as info:
        ingest(source, tmp_path / "work", _settings(tmp_path))
    assert info.value.code == "unsupported_file"
    assert "media.mp4" in str(info.value)  # the copy that was probed


def test_corrupt_file_is_rejected(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    source = make_corrupt_media(tmp_path / "corrupt.mp4")
    with pytest.raises(IngestFailure) as info:
        ingest(source, tmp_path / "work", _settings(tmp_path))
    assert info.value.code == "unsupported_file"


def test_truncated_mp4_is_rejected(tmp_path: Path) -> None:
    """A moov-atom-less prefix of a valid MP4 must fail cleanly, not crash."""
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    good = make_video_with_audio(tmp_path / "good.mp4", 3.0)
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(good.read_bytes()[: good.stat().st_size // 3])
    with pytest.raises(IngestFailure) as info:
        ingest(truncated, tmp_path / "work", _settings(tmp_path))
    assert info.value.code == "unsupported_file"


def test_audio_only_file_is_rejected_without_a_video_stream(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    audio = tmp_path / "voice.mp3"
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "libmp3lame",
            str(audio),
        ]
    )
    with pytest.raises(IngestFailure) as info:
        ingest(audio, tmp_path / "work", _settings(tmp_path))
    assert info.value.code == "unsupported_file"
    assert "video stream" in str(info.value)


def test_video_without_audio_is_rejected(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    source = make_video_no_audio(tmp_path / "silent.mp4")
    with pytest.raises(IngestFailure) as info:
        ingest(source, tmp_path / "work", _settings(tmp_path))
    assert info.value.code == "no_audio"


def test_unsupported_extension_with_valid_content_is_rejected(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    # Same bytes as a valid MP4, only the extension is wrong.
    good = make_video_with_audio(tmp_path / "really-video.mp4", 2.0)
    disguised = tmp_path / "actually-video.bin"
    disguised.write_bytes(good.read_bytes())
    with pytest.raises(IngestFailure) as info:
        ingest(disguised, tmp_path / "work", _settings(tmp_path))
    assert info.value.code == "unsupported_file"
    assert "extension" in str(info.value)


def test_valid_extension_with_wrong_content_is_rejected(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    fake = tmp_path / "lies.mp4"
    fake.write_text("this is plain text, not an mp4", encoding="utf-8")
    with pytest.raises(IngestFailure) as info:
        ingest(fake, tmp_path / "work", _settings(tmp_path))
    assert info.value.code == "unsupported_file"


# ---------- durations ----------


def test_extremely_short_video_is_ingested(tmp_path: Path) -> None:
    """A 0.6 s source must ingest: short clips are trimmed later, not refused."""
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    source = make_video_with_audio(tmp_path / "blink.mp4", 0.6)
    media = ingest(source, tmp_path / "work", _settings(tmp_path))
    assert 0.0 < media.duration < 2.0
    assert media.has_audio


def test_duration_guard_uses_faked_probe(tmp_path: Path) -> None:
    """The 240-minute guard is tested with a faked probe, never a huge file."""
    from openclip.core.models import MediaInfo

    settings = _settings(tmp_path)
    too_long = MediaInfo(
        path="/tmp/fake.mp4",
        duration=settings.max_video_minutes * 60 + 1,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    with pytest.raises(IngestFailure) as info:
        check_ingest_limits(too_long, settings)
    assert info.value.code == "too_long"
    assert "240" in str(info.value)

    at_limit = too_long.model_copy(update={"duration": settings.max_video_minutes * 60})
    check_ingest_limits(at_limit, settings)  # exactly at the limit is allowed


def test_duration_guard_is_configurable(tmp_path: Path) -> None:
    from openclip.core.models import MediaInfo

    settings = _settings(tmp_path)
    settings.max_video_minutes = 1
    media = MediaInfo(
        path="/tmp/fake.mp4",
        duration=61.0,
        width=640,
        height=360,
        fps=25.0,
        has_audio=True,
    )
    with pytest.raises(IngestFailure) as info:
        check_ingest_limits(media, settings)
    assert info.value.code == "too_long" and "1 minute" in str(info.value)


# ---------- filenames ----------


@pytest.mark.parametrize(
    "name",
    [
        "clip with spaces.mp4",
        "café-thé.mp4",
        "[bracket] (paren).mp4",
        "quote'and\"double.mp4",
        "-leading-dash.mp4",
        "ünïcodé-ダッシュ.mp4",
    ],
)
def test_odd_filenames_ingest_cleanly(tmp_path: Path, name: str) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    good = make_video_with_audio(tmp_path / name, 1.5)
    workdir = tmp_path / "work"
    media = ingest(good, workdir, _settings(tmp_path))
    assert Path(media.path).is_file()
    # The copy always uses a canonical name, so odd source names never leak.
    assert Path(media.path).name == "media.mp4"
    assert (workdir / "audio.wav").is_file()


def test_very_long_filename_ingests(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    name = "a" * 200 + ".mp4"
    good = make_video_with_audio(tmp_path / name, 1.5)
    media = ingest(good, tmp_path / "work", _settings(tmp_path))
    assert Path(media.path).name == "media.mp4"


def test_missing_file_and_directory_are_rejected(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    settings = _settings(tmp_path)
    with pytest.raises(IngestFailure) as info:
        ingest(tmp_path / "nope.mp4", tmp_path / "work", settings)
    assert info.value.code == "not_found"
    folder = tmp_path / "folder.mp4"
    folder.mkdir()
    with pytest.raises(IngestFailure) as info:
        ingest(folder, tmp_path / "work2", settings)
    assert info.value.code == "unsupported_file"


# ---------- unusual geometry ----------


def _probe(path: Path, settings: Settings):
    return probe_media(path, settings)


def test_vertical_316_source_probes_ok(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    source = tmp_path / "vertical.mp4"
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=180x320:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ]
    )
    media = _probe(source, _settings(tmp_path))
    assert media.height > media.width
    assert (media.width, media.height) == (180, 320)


def test_very_low_and_very_wide_sources_probe_ok(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    settings = _settings(tmp_path)
    for name, size in (("tiny.mp4", "64x48"), ("wide.mp4", "640x64")):
        source = tmp_path / name
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=duration=1:size={size}:rate=10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(source),
            ]
        )
        media = _probe(source, settings)
        assert media.duration > 0 and media.width > 0 and media.height > 0
        assert media.has_audio is False


def test_variable_frame_rate_source_probes_finite_fps(tmp_path: Path) -> None:
    """VFR input must yield a positive, finite fps (ffprobe r_frame_rate)."""
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    source = tmp_path / "vfr.mp4"
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=160x120:rate=30",
            "-vf",
            "select='not(mod(n,7))',setpts=N/FRAME_RATE/TB",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(source),
        ]
    )
    media = _probe(source, _settings(tmp_path))
    assert media.fps > 0
    assert media.fps < 1000  # never inf/nan-ish garbage


# ---------- URLs ----------


def test_invalid_and_unsupported_urls_never_reach_the_network(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for url, expected in [
        ("ftp://example.com/x.mp4", "invalid_url"),
        ("file:///etc/passwd", "invalid_url"),
        ("http://127.0.0.1:8080/x.mp4", "invalid_url"),
        ("http://localhost/x.mp4", "invalid_url"),
        ("http://[::1]/x.mp4", "invalid_url"),
        ("http://169.254.169.254/latest/meta-data/", "invalid_url"),
        ("http://nonexistent.invalid.tld.example/x.mp4", "invalid_url"),
    ]:
        with pytest.raises(IngestFailure) as info:
            ingest(url, tmp_path / ("work-" + expected), settings)
        assert info.value.code == expected, url


def test_ingest_rejects_inverted_job_source_contract() -> None:
    from pydantic import ValidationError

    from openclip.core.models import JobSource

    for value in ("not-a-url", "javascript:alert(1)", "/etc/passwd"):
        with pytest.raises(ValidationError):
            JobSource(type="url", value=value)


def test_download_refuses_when_ytdlp_missing(tmp_path: Path, monkeypatch) -> None:
    """A URL source with yt-dlp absent fails with download_failed, not a crash."""
    from openclip.ingest import download as download_mod

    monkeypatch.setattr(download_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        download_mod,
        "validate_download_url",
        lambda url, resolver=None: url,
    )
    with pytest.raises(IngestFailure) as info:
        download_mod.download_video("https://example.com/video", tmp_path, _settings(tmp_path))
    assert info.value.code == "download_failed"
    assert "yt-dlp" in str(info.value)


def test_ingest_writes_media_json_with_probed_values(tmp_path: Path) -> None:
    if not _ffmpeg():
        pytest.skip("ffmpeg/ffprobe not installed")
    source = make_video_with_audio(tmp_path / "source.mp4", 2.0)
    workdir = tmp_path / "work"
    media = ingest(source, workdir, _settings(tmp_path))
    payload = json.loads((workdir / "media.json").read_text())
    assert payload["path"] == media.path
    assert payload["has_audio"] is True
    assert (workdir / "audio.wav").stat().st_size > 0
