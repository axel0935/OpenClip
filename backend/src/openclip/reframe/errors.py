"""Reframe-stage errors with stable machine-readable codes."""

from openclip.core.errors import OpenClipError


class ReframeError(OpenClipError):
    """A reframing failure that also carries a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
