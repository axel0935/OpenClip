"""Fixed stage interfaces implemented by later pipeline work."""

from pathlib import Path
from typing import Protocol

from openclip.core.models import (
    Candidate,
    CaptionStyle,
    Clip,
    CropPath,
    JobOptions,
    JobSource,
    MediaInfo,
    Transcript,
)


class Ingestor(Protocol):
    """Acquire a source into a job work directory."""

    def ingest(self, source: JobSource, workdir: Path) -> MediaInfo: ...


class Transcriber(Protocol):
    """Create a word-level transcript for source media."""

    def transcribe(self, media: MediaInfo, cfg: JobOptions) -> Transcript: ...


class Analyzer(Protocol):
    """Discover, score, and select candidate clips."""

    def find_candidates(
        self, transcript: Transcript, media: MediaInfo, cfg: JobOptions
    ) -> list[Candidate]: ...

    def score_candidates(
        self, candidates: list[Candidate], transcript: Transcript, cfg: JobOptions
    ) -> list[Clip]: ...

    def select_clips(self, clips: list[Clip], cfg: JobOptions) -> list[Clip]: ...


class Reframer(Protocol):
    """Compute a vertical crop path for a clip."""

    def compute_crop_path(self, media: MediaInfo, clip: Clip, layout: str) -> CropPath: ...


class CaptionBuilder(Protocol):
    """Build an ASS subtitle document for a clip."""

    def build_ass(self, transcript: Transcript, clip: Clip, style: CaptionStyle) -> str: ...


class Renderer(Protocol):
    """Render one output clip from source media and stage artifacts."""

    def render_clip(
        self,
        media: MediaInfo,
        clip: Clip,
        crop: CropPath,
        ass_path: Path,
        out_path: Path,
        cfg: JobOptions,
    ) -> Path: ...
