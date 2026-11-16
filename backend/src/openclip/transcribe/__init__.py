"""Word-level transcription powered by faster-whisper."""

from openclip.transcribe.config import ResolvedModel, TranscribeConfig, resolve_profile
from openclip.transcribe.errors import TranscriptionFailure
from openclip.transcribe.service import transcribe

__all__ = [
    "ResolvedModel",
    "TranscribeConfig",
    "TranscriptionFailure",
    "resolve_profile",
    "transcribe",
]
