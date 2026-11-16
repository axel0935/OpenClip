"""Typed exceptions used by OpenClip stages."""


class OpenClipError(Exception):
    """Base exception for expected OpenClip failures."""


class IngestError(OpenClipError):
    """Raised when media cannot be acquired or inspected."""


class TranscriptionError(OpenClipError):
    """Raised when transcription fails."""


class AnalysisError(OpenClipError):
    """Raised when highlight analysis fails."""


class RenderError(OpenClipError):
    """Raised when rendering or export fails."""


class ConfigError(OpenClipError):
    """Raised when local configuration is invalid."""
