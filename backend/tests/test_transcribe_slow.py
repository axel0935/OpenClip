"""Slow tests that run the real Whisper model (excluded from default runs)."""

from pathlib import Path

import pytest

from openclip.core.config import Settings
from openclip.core.models import MediaInfo
from openclip.transcribe import TranscribeConfig, transcribe
from tests.media import make_speech_wav


def _require_asr() -> None:
    pytest.importorskip("faster_whisper", reason="faster-whisper extra not installed")


@pytest.mark.slow
def test_real_base_en_transcribes_speech(tmp_path: Path) -> None:
    """End-to-end smoke test with the real ``base.en`` model."""
    _require_asr()
    workdir = tmp_path / "work"
    workdir.mkdir()
    speech = make_speech_wav(workdir / "speech.wav")
    # The pipeline expects audio.wav inside the workdir.
    (workdir / "audio.wav").write_bytes(speech.read_bytes())
    media = MediaInfo(
        path=str(speech),
        duration=10.0,
        width=320,
        height=240,
        fps=15.0,
        has_audio=True,
    )
    settings = Settings(data_dir=tmp_path / "data")
    transcript = transcribe(
        media,
        TranscribeConfig(profile="fast", cpu_threads=6),
        workdir=workdir,
        settings=settings,
    )
    assert transcript.language == "en"
    assert transcript.model == "base.en"
    assert transcript.segments, "expected at least one transcript segment"
    word_count = sum(len(s.words) for s in transcript.segments)
    assert word_count > 0
