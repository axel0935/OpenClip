"""Pydantic data contracts shared by every OpenClip module."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$", examples=["#FFFFFF"])]
KebabId = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", examples=["clean-pop"])]


class TimeRangeModel(BaseModel):
    """Base model that ensures an end time is not before a start time."""

    @model_validator(mode="after")
    def validate_time_range(self) -> "TimeRangeModel":
        """Reject inverted time intervals."""
        values = self.__dict__
        if values["end"] < values["start"]:
            raise ValueError("end must be greater than or equal to start")
        return self


class Word(TimeRangeModel):
    """A word with timestamps and optional transcription confidence."""

    text: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    probability: float | None = Field(default=None, ge=0, le=1)


class Segment(TimeRangeModel):
    """A transcript sentence or speech segment."""

    id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)
    words: list[Word] = Field(default_factory=list)


class Transcript(BaseModel):
    """Timestamped transcript produced by an ASR model."""

    language: str = Field(min_length=1)
    duration: float = Field(ge=0)
    model: str = Field(min_length=1)
    segments: list[Segment] = Field(default_factory=list)


class MediaInfo(BaseModel):
    """Metadata for an acquired source media file."""

    path: str = Field(min_length=1)
    duration: float = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    has_audio: bool
    title: str | None = None
    source_url: str | None = None


class CandidateFeatures(BaseModel):
    """Objective signals used to rank a candidate moment."""

    speech_rate_wps: float = Field(ge=0)
    audio_energy: float = Field(ge=0, le=1)
    question_count: int = Field(ge=0)
    scene_changes: int = Field(ge=0)
    hook_score: float = Field(ge=0, le=1)


class Candidate(TimeRangeModel):
    """An analyzable source interval before final clip selection."""

    id: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)
    features: CandidateFeatures
    heuristic_score: float = Field(ge=0, le=100)


class ClipSource(StrEnum):
    """The ranking mechanism that selected a clip."""

    LLM = "llm"
    HEURISTIC = "heuristic"


class Clip(TimeRangeModel):
    """A selected output clip and its editorial metadata."""

    id: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    score: float = Field(ge=0, le=100)
    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    hashtags: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    source: ClipSource
    output_file: str | None = None
    thumbnail: str | None = None


class CropKeyframe(BaseModel):
    """One source-pixel crop rectangle at a clip-relative timestamp."""

    t: float = Field(ge=0)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class CropPath(BaseModel):
    """A vertical crop trajectory for one output clip."""

    layout: Literal["fill", "fit"]
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    keyframes: list[CropKeyframe] = Field(min_length=1)


class CaptionBackground(BaseModel):
    """Optional background plate behind captions."""

    color: Color
    padding: int = Field(ge=0)
    radius: int = Field(ge=0)


class CaptionStyle(BaseModel):
    """Serializable visual preset for ASS caption rendering."""

    id: KebabId
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    font_family: str = Field(min_length=1)
    bold: bool
    italic: bool = False
    font_size: int = Field(gt=0)
    uppercase: bool
    letter_spacing: float = 0
    text_color: Color
    highlight_color: Color
    outline_color: Color
    outline_width: float = Field(ge=0)
    shadow_color: Color | None = None
    shadow_offset: float = Field(default=0, ge=0)
    background: CaptionBackground | None = None
    highlight_mode: Literal["none", "word_color", "word_box", "karaoke_fill"]
    animation: Literal["none", "pop", "fade", "slide_up", "bounce"]
    words_per_caption: int = Field(ge=1, le=6)
    max_lines: int = Field(ge=1, le=3)
    position: Literal["top", "middle", "bottom"]
    margin_v: int = Field(ge=0)


class Stage(StrEnum):
    """A pipeline stage with observable progress."""

    INGEST = "ingest"
    TRANSCRIBE = "transcribe"
    SEGMENT = "segment"
    SCORE = "score"
    SELECT = "select"
    REFRAME = "reframe"
    CAPTIONS = "captions"
    RENDER = "render"
    EXPORT = "export"


class JobStatus(StrEnum):
    """Lifecycle state of a clip-generation job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobSource(BaseModel):
    """User-selected URL or uploaded file reference."""

    type: Literal["url", "upload"]
    value: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_url_source(self) -> "JobSource":
        """Restrict explicit remote sources to normal HTTP(S) URLs."""
        if self.type == "url":
            parsed = urlparse(self.value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("URL sources must use an absolute HTTP(S) URL")
        return self


class JobOptions(BaseModel):
    """User-selectable job settings with safe local defaults."""

    min_clip_seconds: float = Field(default=20, gt=0)
    max_clip_seconds: float = Field(default=90, gt=0)
    max_clips: int = Field(default=8, gt=0)
    caption_style_id: KebabId = "clean-pop"
    layout: Literal["auto", "fill", "fit"] = "auto"
    whisper_profile: Literal["auto", "fast", "balanced", "quality"] = "auto"
    analysis_mode: Literal["auto", "heuristic", "llm"] = "auto"

    @model_validator(mode="after")
    def validate_clip_duration_range(self) -> "JobOptions":
        """Ensure clip maxima do not fall below their minima."""
        if self.max_clip_seconds < self.min_clip_seconds:
            raise ValueError("max_clip_seconds must be at least min_clip_seconds")
        return self


class JobError(BaseModel):
    """Structured non-sensitive error details for a failed job."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class Job(BaseModel):
    """Persisted state and outputs of a requested clipping job."""

    id: str = Field(min_length=1)
    status: JobStatus
    stage: Stage | None = None
    progress: float = Field(ge=0, le=1)
    message: str | None = None
    source: JobSource
    options: JobOptions = Field(default_factory=JobOptions)
    media: MediaInfo | None = None
    clips: list[Clip] = Field(default_factory=list)
    error: JobError | None = None
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    """Minimal service liveness response."""

    status: Literal["ok"]
    version: str


class UploadResponse(BaseModel):
    """Reference returned after a local file upload is accepted."""

    upload_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class JobCreate(BaseModel):
    """Request body for creating a clip-generation job."""

    source: JobSource
    options: JobOptions = Field(default_factory=JobOptions)


class ClipUpdate(BaseModel):
    """Optional timing and caption-preset changes for a selected clip."""

    model_config = ConfigDict(extra="forbid")

    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)
    caption_style_id: KebabId | None = None

    @model_validator(mode="after")
    def validate_optional_time_range(self) -> "ClipUpdate":
        """Validate a complete edited range when both limits are supplied."""
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self


class ErrorResponse(BaseModel):
    """Standard public API error response."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
