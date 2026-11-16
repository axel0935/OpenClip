"""Safe media acquisition from local files and public URLs."""

from openclip.ingest.errors import IngestFailure
from openclip.ingest.service import extract_audio, ingest

__all__ = ["IngestFailure", "extract_audio", "ingest"]
