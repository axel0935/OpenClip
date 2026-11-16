"""Tests for transcription config, caching, and transcript conversion."""

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from openclip.core.errors import TranscriptionError
from openclip.core.models import JobOptions, MediaInfo, Transcript
from openclip.transcribe import TranscribeConfig, resolve_profile, transcribe
from openclip.transcribe.cache import cache_key, cache_params, load_cached
from openclip.transcribe.errors import TranscriptionFailure
from openclip.transcribe.service import _load_model
from tests.media import make_tone_wav


def _media(workdir: Path) -> MediaInfo:
    return MediaInfo(
        path=str(workdir / "media.mp4"),
        duration=4.0,
        width=320,
        height=240,
        fps=15.0,
        has_audio=True,
    )


class _FakeWord:
    def __init__(self, word: str, start: float | None, end: float | None, prob: Any = 0.9):
        self.word = word
        self.start = start
        self.end = end
        self.probability = prob


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str, words: list[Any] | None = None):
        self.start = start
        self.end = end
        self.text = text
        self.words = words


class _FakeInfo:
    duration = 4.0
    language = "en"


class _FakeModel:
    """Stand-in for faster-whisper's WhisperModel (no download)."""

    def __init__(self, segments: list[Any], calls: list[int]):
        self._segments = segments
        self._calls = calls

    def transcribe(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        self._calls[0] += 1

        def _gen() -> Any:
            yield from self._segments

        return _gen(), _FakeInfo()


def _patch_model(monkeypatch: pytest.MonkeyPatch, model: _FakeModel) -> None:
    def fake_load(*args: Any, **kwargs: Any) -> _FakeModel:
        return model

    monkeypatch.setattr("openclip.transcribe.service._load_model", fake_load)


def test_resolve_profile_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Profiles map to concrete Whisper models and decoding settings."""
    import os

    assert resolve_profile(TranscribeConfig(profile="fast")).model == "base.en"
    assert resolve_profile(TranscribeConfig(profile="balanced")).model == "small.en"
    assert resolve_profile(TranscribeConfig(profile="quality")).model == "large-v3-turbo"
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    weak = resolve_profile(TranscribeConfig(profile="auto"))
    assert weak.model == "base.en"
    assert weak.device == "cpu"
    assert weak.compute_type == "int8"
    assert weak.beam_size == 1
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    strong = resolve_profile(TranscribeConfig(profile="auto"))
    assert strong.model == "small.en"
    assert strong.beam_size == 5
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    measured = resolve_profile(TranscribeConfig(profile="auto"), measured_rtf=0.4)
    assert measured.model == "small.en"
    assert resolve_profile(TranscribeConfig(profile="auto"), measured_rtf=0.9).model == "base.en"
    assert resolve_profile(TranscribeConfig(profile="quality")).beam_size == 5
    override = resolve_profile(TranscribeConfig(profile="fast", beam_size=3))
    assert override.beam_size == 3


def test_cache_key_is_stable_and_sensitive(tmp_path: Path) -> None:
    """Cache keys depend on audio content and model parameters."""
    audio = make_tone_wav(tmp_path / "a.wav", seconds=0.5)
    params = cache_params(TranscribeConfig(profile="fast"))
    key1 = cache_key(audio, "base.en", params)
    assert key1 == cache_key(audio, "base.en", params)
    assert key1 != cache_key(audio, "small.en", params)
    other = make_tone_wav(tmp_path / "b.wav", seconds=0.5, freq=880.0)
    assert key1 != cache_key(other, "base.en", params)


def test_cache_roundtrip(tmp_path: Path) -> None:
    """Saved transcripts reload only when the cache key matches."""
    audio = make_tone_wav(tmp_path / "a.wav", seconds=0.2)
    transcript = Transcript(language="en", duration=1.0, model="base.en", segments=[])
    from openclip.transcribe.cache import save_cached

    key = cache_key(audio, "base.en", {})
    save_cached(tmp_path, key, transcript)
    assert load_cached(tmp_path, key) == transcript
    assert load_cached(tmp_path, "different") is None
    payload = json.loads((tmp_path / "transcript.json").read_text())
    assert payload["cache_key"] == key
    assert payload["transcript"]["language"] == "en"


def test_transcribe_converts_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake faster-whisper output becomes a valid Transcript."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    make_tone_wav(workdir / "audio.wav", seconds=1.0)
    segments = [
        _FakeSegment(
            0.0,
            2.0,
            " hello world ",
            [
                _FakeWord("hello", 0.0, 0.5, 0.95),
                _FakeWord("world", 0.5, 1.0, None),
                _FakeWord("", 1.0, 1.1),  # empty text dropped
                _FakeWord("ghost", None, None),  # no timestamps dropped
                _FakeWord("late", None, 1.5, 2.5),  # bad prob + missing start repaired
            ],
        ),
        _FakeSegment(2.0, 3.0, "   "),  # blank text skipped
        _FakeSegment(2.5, 2.0, "reversed"),  # end < start repaired
    ]
    calls = [0]
    _patch_model(monkeypatch, _FakeModel(segments, calls))

    transcript = transcribe(_media(workdir), TranscribeConfig(profile="fast"), workdir=workdir)

    assert isinstance(transcript, Transcript)
    assert transcript.language == "en"
    assert transcript.model == "base.en"
    assert len(transcript.segments) == 2
    first = transcript.segments[0]
    assert first.id == 0
    assert first.text == "hello world"
    texts = [w.text for w in first.words]
    assert texts == ["hello", "world", "late"]
    late = first.words[-1]
    assert late.start == pytest.approx(1.5)
    assert late.end == pytest.approx(1.5)
    assert late.probability is None
    assert transcript.segments[1].start <= transcript.segments[1].end


def test_transcribe_uses_cache_on_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matching transcript.json skips model loading entirely."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    make_tone_wav(workdir / "audio.wav", seconds=0.5)
    calls = [0]
    _patch_model(monkeypatch, _FakeModel([_FakeSegment(0.0, 1.0, "hi")], calls))
    cfg = TranscribeConfig(profile="fast")

    first = transcribe(_media(workdir), cfg, workdir=workdir)
    second = transcribe(_media(workdir), cfg, workdir=workdir)

    assert calls[0] == 1
    assert first == second
    assert (workdir / "transcript.json").is_file()


def test_transcribe_empty_result_strict_and_lenient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No speech yields an empty Transcript unless strict mode is requested."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    make_tone_wav(workdir / "audio.wav", seconds=0.5)
    _patch_model(monkeypatch, _FakeModel([], [0]))

    transcript = transcribe(_media(workdir), TranscribeConfig(), workdir=workdir)
    assert transcript.segments == []

    other = tmp_path / "strict"
    other.mkdir()
    make_tone_wav(other / "audio.wav", seconds=0.5)
    _patch_model(monkeypatch, _FakeModel([], [0]))
    with pytest.raises(TranscriptionFailure) as excinfo:
        transcribe(_media(other), TranscribeConfig(), workdir=other, strict=True)
    assert excinfo.value.code == "no_speech"
    assert isinstance(excinfo.value, TranscriptionError)


def test_transcribe_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A set cancel event aborts transcription with the cancelled code."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    make_tone_wav(workdir / "audio.wav", seconds=0.5)
    _patch_model(monkeypatch, _FakeModel([_FakeSegment(0.0, 1.0, "hi")], [0]))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranscriptionFailure) as excinfo:
        transcribe(_media(workdir), TranscribeConfig(), workdir=workdir, cancel=cancel)
    assert excinfo.value.code == "cancelled"


def test_transcribe_accepts_job_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """JobOptions are normalized to a TranscribeConfig using whisper_profile."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    make_tone_wav(workdir / "audio.wav", seconds=0.5)
    calls = [0]
    _patch_model(monkeypatch, _FakeModel([_FakeSegment(0.0, 1.0, "hi")], calls))
    transcript = transcribe(_media(workdir), JobOptions(whisper_profile="fast"), workdir=workdir)
    assert transcript.model == "base.en"


def test_missing_asr_extra_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without faster-whisper the error explains how to install it."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    make_tone_wav(workdir / "audio.wav", seconds=0.5)

    # Simulate the lazy import failing inside _load_model.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *a: Any, **k: Any) -> Any:
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(TranscriptionFailure) as excinfo:
        _load_model(
            "base.en",
            resolve_profile(TranscribeConfig()),
            TranscribeConfig(),
            tmp_path / "m",
            None,
        )
    assert excinfo.value.code == "asr_unavailable"
    assert "asr" in str(excinfo.value)
