"""Tests for local ingest, URL validation, and ffprobe parsing."""

import shutil
from pathlib import Path

import pytest

from openclip.core.config import Settings
from openclip.core.errors import IngestError
from openclip.core.models import MediaInfo
from openclip.ingest import ingest
from openclip.ingest.errors import IngestFailure
from openclip.ingest.ffprobe import probe_media
from openclip.ingest.urlcheck import validate_download_url
from tests.media import (
    make_corrupt_media,
    make_video_no_audio,
    make_video_with_audio,
)

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def ffmpeg_ok() -> None:
    """Skip media tests when FFmpeg tooling is unavailable."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/video.mp4",
        "https://",
        "not-a-url",
    ],
)
def test_validate_url_rejects_bad_schemes(url: str) -> None:
    """Only absolute http(s) URLs are accepted."""
    with pytest.raises(IngestFailure) as excinfo:
        validate_download_url(url)
    assert excinfo.value.code == "invalid_url"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/video.mp4",
        "http://127.0.0.1/video.mp4",
        "http://10.0.0.5/video.mp4",
        "http://192.168.1.10/video.mp4",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/video.mp4",
        "http://0.0.0.0/video.mp4",
    ],
)
def test_validate_url_blocks_private_addresses(url: str) -> None:
    """Loopback, private, link-local and reserved targets are refused."""
    with pytest.raises(IngestFailure) as excinfo:
        validate_download_url(url)
    assert excinfo.value.code == "invalid_url"


def test_validate_url_blocks_dns_rebinding_to_private() -> None:
    """A public hostname resolving to a private IP is refused."""

    def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "192.168.0.10"]

    with pytest.raises(IngestFailure) as excinfo:
        validate_download_url("https://example.com/video.mp4", resolver=resolver)
    assert excinfo.value.code == "invalid_url"


def test_validate_url_accepts_public_address() -> None:
    """A hostname resolving only to public IPs passes validation."""

    def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC_IP]

    assert validate_download_url("https://example.com/video.mp4", resolver=resolver).startswith(
        "https://"
    )


def test_probe_media_parses_ffprobe_json(tmp_path: Path, ffmpeg_ok: None) -> None:
    """ffprobe output maps onto the MediaInfo contract."""
    video = make_video_with_audio(tmp_path / "clip.mp4")
    media = probe_media(video, Settings())
    assert media.width == 320
    assert media.height == 240
    assert media.fps == pytest.approx(15.0)
    assert media.has_audio is True
    assert media.duration == pytest.approx(3.0, abs=0.5)


def test_ingest_local_file(tmp_path: Path, ffmpeg_ok: None) -> None:
    """A local video is copied into the workdir with an audio WAV."""
    source = make_video_with_audio(tmp_path / "source.mp4")
    workdir = tmp_path / "work"
    media = ingest(str(source), workdir)
    assert isinstance(media, MediaInfo)
    assert media.has_audio is True
    assert Path(media.path).parent == workdir
    assert (workdir / "audio.wav").is_file()
    assert (workdir / "media.json").is_file()
    assert media.source_url is None


def test_ingest_no_audio_video(tmp_path: Path, ffmpeg_ok: None) -> None:
    """Video without an audio track fails with the no_audio code."""
    source = make_video_no_audio(tmp_path / "silent.mp4")
    with pytest.raises(IngestError) as excinfo:
        ingest(str(source), tmp_path / "work")
    assert getattr(excinfo.value, "code", None) == "no_audio"


def test_ingest_corrupt_file(tmp_path: Path, ffmpeg_ok: None) -> None:
    """A corrupt file fails with the unsupported_file code."""
    source = make_corrupt_media(tmp_path / "broken.mp4")
    with pytest.raises(IngestError) as excinfo:
        ingest(str(source), tmp_path / "work")
    assert getattr(excinfo.value, "code", None) == "unsupported_file"


def test_ingest_missing_file(tmp_path: Path, ffmpeg_ok: None) -> None:
    """A missing path fails with the not_found code."""
    with pytest.raises(IngestError) as excinfo:
        ingest(str(tmp_path / "missing.mp4"), tmp_path / "work")
    assert getattr(excinfo.value, "code", None) == "not_found"


def test_ingest_rejects_bad_extension(tmp_path: Path, ffmpeg_ok: None) -> None:
    """Files with non-media extensions are refused before probing."""
    fake = tmp_path / "notes.txt"
    fake.write_text("hello")
    with pytest.raises(IngestError) as excinfo:
        ingest(str(fake), tmp_path / "work")
    assert getattr(excinfo.value, "code", None) == "unsupported_file"


def test_ingest_too_long(tmp_path: Path, ffmpeg_ok: None) -> None:
    """Media longer than the configured limit fails with too_long."""
    source = make_video_with_audio(tmp_path / "long.mp4", seconds=2.0)
    settings = Settings(max_video_minutes=0)
    with pytest.raises(IngestError) as excinfo:
        ingest(str(source), tmp_path / "work", settings=settings)
    assert getattr(excinfo.value, "code", None) == "too_long"
