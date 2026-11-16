"""Transcription configuration and Whisper profile resolution."""

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


def resolve_profile(cfg: TranscribeConfig) -> ResolvedModel:
    """Map a :class:`TranscribeConfig` to concrete model parameters."""
    profile = "balanced" if cfg.profile == "auto" else cfg.profile
    model = _PROFILE_MODELS[profile]
    device = "cpu" if cfg.device == "auto" else cfg.device
    compute_type = cfg.compute_type or ("int8" if device == "cpu" else "float16")
    beam_size = cfg.beam_size or _PROFILE_BEAM_SIZES[profile]
    return ResolvedModel(model=model, beam_size=beam_size, device=device, compute_type=compute_type)
