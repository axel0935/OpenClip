"""Ingest errors carrying stable machine-readable codes."""

from openclip.core.errors import IngestError


class IngestFailure(IngestError):
    """An :class:`IngestError` that also carries a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
