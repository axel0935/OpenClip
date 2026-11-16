"""Transcription configuration and Whisper profile resolution."""

import os
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

WhisperProfile = Literal["auto", "fast", "balanced", "quality"]

_PROFILE_MODELS: dict[str, str] = {
    "fast": "base.en",
    "balanced": "small.en",
    "quality": "large-v3-turbo",
}

# Measured on the N305 reference machine (57 s flite speech, small.en int8):
# beam_size=5 ran at RTF 1.48 vs 1.69 for beam_size=1 with equivalent output,
# so balanced keeps beam search. The fast profile stays greedy for speed.
_PROFILE_BEAM_SIZES: dict[str, int] = {
    "fast": 1,
    "balanced": 5,
    "quality": 5,
}


# Measured on the N305 reference machine (8 threads, 179 s sample video):
# fast (base.en) RTF 0.08 / 0.49 GB peak RSS, balanced (small.en) RTF 0.33 /
# 0.87 GB. Transcript differences were minor (punctuation and compound-word
# splits), so weak machines default to fast; strong machines get balanced.
_STRONG_LOGICAL_CPUS = 12
_MEASURED_RTF_STRONG = 0.5


def _auto_profile(measured_rtf: float | None = None) -> str:
    """Choose the ``auto`` profile from machine strength.

    Returns ``balanced`` when the machine has at least
    :data:`_STRONG_LOGICAL_CPUS` logical cores or the caller supplies a
    measured realtime factor below :data:`_MEASURED_RTF_STRONG`; otherwise
    ``fast``. Callers may always override by passing an explicit profile.
    """
    if measured_rtf is not None and measured_rtf < _MEASURED_RTF_STRONG:
        return "balanced"
    if (os.cpu_count() or 0) >= _STRONG_LOGICAL_CPUS:
        return "balanced"
    return "fast"


class TranscribeConfig(BaseModel):
    """User-tunable settings for the transcription stage."""

    profile: WhisperProfile = "auto"
    language: str = Field(default="en", min_length=2, max_length=16)
    cpu_threads: int = Field(default=6, gt=0)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str | None = None
    beam_size: int | None = Field(default=None, gt=0)


@dataclass(frozen=True)
class ResolvedModel:
    """Concrete faster-whisper parameters derived from a profile."""

    model: str
    beam_size: int
    device: str
    compute_type: str


def resolve_profile(cfg: TranscribeConfig, measured_rtf: float | None = None) -> ResolvedModel:
    """Map a :class:`TranscribeConfig` to concrete model parameters.

    ``measured_rtf`` optionally carries a realtime factor measured on this
    machine; values below 0.5 mark it strong enough for ``balanced``.
    """
    profile = _auto_profile(measured_rtf) if cfg.profile == "auto" else cfg.profile
    model = _PROFILE_MODELS[profile]
    device = "cpu" if cfg.device == "auto" else cfg.device
    compute_type = cfg.compute_type or ("int8" if device == "cpu" else "float16")
    beam_size = cfg.beam_size or _PROFILE_BEAM_SIZES[profile]
    return ResolvedModel(model=model, beam_size=beam_size, device=device, compute_type=compute_type)
