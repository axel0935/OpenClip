"""Word-level transcription with faster-whisper (optional ``asr`` extra)."""

import gc
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from openclip.core.config import Settings
from openclip.core.models import JobOptions, MediaInfo, Segment, Transcript, Word
from openclip.ingest.service import extract_audio
from openclip.transcribe.cache import cache_key, cache_params, load_cached, save_cached
from openclip.transcribe.config import ResolvedModel, TranscribeConfig, resolve_profile
from openclip.transcribe.errors import TranscriptionFailure

ProgressCallback = Callable[[float, str], None]


class _WhisperSegmentLike(Protocol):
    """Structural view of a faster-whisper segment."""

    start: float
    end: float
    text: str
    words: list[Any] | None


class _WhisperModelLike(Protocol):
    """Structural view of a faster-whisper model for testability."""

    def transcribe(
        self, *args: Any, **kwargs: Any
    ) -> tuple[Iterable[_WhisperSegmentLike], Any]: ...


def _no_op_progress(_fraction: float, _message: str) -> None:
    """Default progress sink."""


def _report(progress: ProgressCallback | None, fraction: float, message: str) -> None:
    (progress or _no_op_progress)(max(0.0, min(1.0, fraction)), message)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise TranscriptionFailure("cancelled", "Transcription cancelled.")


def _model_is_cached(model_dir: Path, model_name: str) -> bool:
    """Return True when the model snapshot already exists locally."""
    snapshot_dir = model_dir / f"models--Systran--faster-whisper-{model_name}"
    return snapshot_dir.is_dir() and any(snapshot_dir.glob("snapshots/*"))


def _load_model(
    model_name: str,
    resolved: ResolvedModel,
    cfg: TranscribeConfig,
    model_dir: Path,
    progress: ProgressCallback | None,
) -> _WhisperModelLike:
    """Instantiate faster-whisper, downloading weights on first use."""
    try:
        from faster_whisper import WhisperModel  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise TranscriptionFailure(
            "asr_unavailable",
            "faster-whisper is not installed. Install the asr extra, e.g. "
            '`pip install "openclip[asr]"`, then run `openclip doctor`.',
        ) from exc
    model_dir.mkdir(parents=True, exist_ok=True)
    if not _model_is_cached(model_dir, model_name):
        _report(
            progress,
            0.0,
            f"Downloading Whisper model '{model_name}' to {model_dir} (first run only)",
        )
    else:
        _report(progress, 0.0, f"Loading Whisper model '{model_name}'")
    return WhisperModel(
        model_name,
        device=resolved.device,
        compute_type=resolved.compute_type,
        cpu_threads=cfg.cpu_threads,
        download_root=str(model_dir),
    )


def _convert_words(raw_words: Iterable[Any] | None) -> list[Word]:
    """Convert faster-whisper words to contract :class:`Word` objects.

    Words without usable timestamps are repaired from the segment bounds or
    dropped; invalid probabilities are discarded. Output never violates the
    ``Word`` contract.
    """
    words: list[Word] = []
    if not raw_words:
        return words
    for raw in raw_words:
        text = str(getattr(raw, "word", "") or "").strip()
        if not text:
            continue
        start = getattr(raw, "start", None)
        end = getattr(raw, "end", None)
        if start is None and end is None:
            continue
        start_f = float(start) if start is not None else float(end or 0.0)
        end_f = float(end) if end is not None else start_f
        if end_f < start_f:
            start_f, end_f = end_f, start_f
        start_f = max(0.0, start_f)
        probability = getattr(raw, "probability", None)
        if probability is not None and not (0.0 <= float(probability) <= 1.0):
            probability = None
        words.append(
            Word(
                text=text,
                start=start_f,
                end=end_f,
                probability=None if probability is None else float(probability),
            )
        )
    return words


def _to_transcribe_config(cfg: JobOptions | TranscribeConfig | None) -> TranscribeConfig:
    """Normalize the accepted config types to :class:`TranscribeConfig`."""
    if cfg is None:
        return TranscribeConfig()
    if isinstance(cfg, TranscribeConfig):
        return cfg
    return TranscribeConfig(profile=cfg.whisper_profile)


def _ensure_audio(
    media: MediaInfo, workdir: Path, settings: Settings, progress: ProgressCallback | None
) -> Path:
    """Return the 16 kHz mono WAV for ``media``, extracting it if needed."""
    audio_path = workdir / "audio.wav"
    if audio_path.is_file() and audio_path.stat().st_size > 0:
        return audio_path
    _report(progress, 0.02, "Extracting audio track")

    def _scaled(fraction: float, message: str) -> None:
        _report(progress, 0.02 + 0.03 * fraction, message)

    return extract_audio(Path(media.path), workdir, settings, _scaled)


def transcribe(
    media: MediaInfo,
    cfg: JobOptions | TranscribeConfig | None = None,
    *,
    workdir: Path | None = None,
    settings: Settings | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    strict: bool = False,
) -> Transcript:
    """Transcribe ``media`` into a word-level :class:`Transcript`.

    ``cfg`` accepts a :class:`JobOptions` (profile taken from
    ``whisper_profile``) or a :class:`TranscribeConfig`. Results are cached in
    ``workdir/transcript.json`` keyed by the audio hash plus model parameters.
    """
    tcfg = _to_transcribe_config(cfg)
    cfg_settings = settings or Settings()
    workdir = Path(workdir) if workdir is not None else Path(media.path).parent
    resolved = resolve_profile(tcfg)
    audio_path = _ensure_audio(media, workdir, cfg_settings, progress)

    params = cache_params(tcfg)
    params["resolved"] = {
        "model": resolved.model,
        "beam_size": resolved.beam_size,
        "device": resolved.device,
        "compute_type": resolved.compute_type,
    }
    key = cache_key(audio_path, resolved.model, params)
    cached = load_cached(workdir, key)
    if cached is not None:
        _report(progress, 1.0, "Loaded cached transcript")
        return cached

    model_dir = cfg_settings.data_dir / "models"
    model: _WhisperModelLike | None = _load_model(
        resolved.model, resolved, tcfg, model_dir, progress
    )
    duration = media.duration
    language = tcfg.language
    segments: list[Segment] = []
    try:
        _report(progress, 0.05, "Running speech recognition")
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=tcfg.language,
            word_timestamps=True,
            vad_filter=True,
            beam_size=resolved.beam_size,
            condition_on_previous_text=False,
        )
        info_duration = float(getattr(info, "duration", 0.0) or 0.0)
        duration = info_duration or media.duration
        language = str(getattr(info, "language", "") or tcfg.language)
        last_bucket = -1
        for raw_segment in segments_iter:
            _check_cancel(cancel)
            seg_start = max(0.0, float(getattr(raw_segment, "start", 0.0) or 0.0))
            seg_end = max(seg_start, float(getattr(raw_segment, "end", seg_start) or seg_start))
            text = str(getattr(raw_segment, "text", "") or "").strip()
            if not text:
                continue
            segments.append(
                Segment(
                    id=len(segments),
                    start=seg_start,
                    end=seg_end,
                    text=text,
                    words=_convert_words(getattr(raw_segment, "words", None)),
                )
            )
            if duration > 0:
                fraction = min(seg_end / duration, 1.0)
                bucket = int(fraction * 100)
                if bucket != last_bucket:
                    last_bucket = bucket
                    _report(
                        progress,
                        0.05 + 0.9 * fraction,
                        f"Transcribing ({bucket}%)",
                    )
    except TranscriptionFailure:
        raise
    except Exception as exc:
        raise TranscriptionFailure(
            "transcription_failed", f"Speech recognition failed: {exc}"
        ) from exc
    finally:
        del model
        gc.collect()

    transcript = Transcript(
        language=language,
        duration=duration,
        model=resolved.model,
        segments=segments,
    )
    save_cached(workdir, key, transcript)
    if strict and not transcript.segments:
        raise TranscriptionFailure("no_speech", "No speech was detected in the audio.")
    _report(progress, 1.0, "Transcription complete")
    return transcript
