"""Contract validation and serialization tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from openclip.core.models import (
    Candidate,
    CandidateFeatures,
    CaptionBackground,
    CaptionStyle,
    Clip,
    ClipSource,
    ClipUpdate,
    CropKeyframe,
    CropPath,
    ErrorResponse,
    HealthResponse,
    Job,
    JobCreate,
    JobError,
    JobOptions,
    JobSource,
    JobStatus,
    MediaInfo,
    Segment,
    Stage,
    Transcript,
    UploadResponse,
    Word,
)


def test_contract_models_round_trip_json() -> None:
    """Every public contract model serializes and validates JSON."""
    word = Word(text="Hello", start=0.0, end=0.2, probability=0.99)
    segment = Segment(id=0, start=0.0, end=0.2, text="Hello", words=[word])
    transcript = Transcript(language="en", duration=0.2, model="base.en", segments=[segment])
    media = MediaInfo(
        path="/tmp/input.mp4", duration=12.0, width=1920, height=1080, fps=30.0, has_audio=True
    )
    features = CandidateFeatures(
        speech_rate_wps=2.4, audio_energy=0.4, question_count=1, scene_changes=0, hook_score=0.8
    )
    candidate = Candidate(
        id="candidate-1", start=0.0, end=12.0, text="Hello", features=features, heuristic_score=80
    )
    clip = Clip(
        id="clip-1",
        start=0.0,
        end=12.0,
        score=90,
        title="Hello",
        hook="Listen up",
        hashtags=["#openclip"],
        rationale="Strong hook",
        source=ClipSource.HEURISTIC,
    )
    crop = CropPath(
        layout="fill",
        source_width=1920,
        source_height=1080,
        keyframes=[CropKeyframe(t=0, x=656, y=0, w=608, h=1080)],
    )
    style = CaptionStyle(
        id="clean-pop",
        name="Clean Pop",
        description="Readable captions",
        font_family="Inter",
        bold=True,
        font_size=64,
        uppercase=False,
        text_color="#FFFFFF",
        highlight_color="#00FF00",
        outline_color="#000000",
        outline_width=2,
        background=CaptionBackground(color="#00000080", padding=8, radius=4),
        highlight_mode="word_color",
        animation="pop",
        words_per_caption=3,
        max_lines=2,
        position="bottom",
        margin_v=80,
    )
    now = datetime.now(UTC)
    job = Job(
        id="job-1",
        status=JobStatus.COMPLETED,
        stage=Stage.EXPORT,
        progress=1,
        source=JobSource(type="upload", value="upload-1"),
        options=JobOptions(),
        media=media,
        clips=[clip],
        error=JobError(code="none", message="No error"),
        created_at=now,
        updated_at=now,
    )
    values = [
        word,
        segment,
        transcript,
        media,
        features,
        candidate,
        clip,
        crop,
        style,
        job,
        HealthResponse(status="ok", version="0.1.0"),
        UploadResponse(upload_id="upload-1", filename="video.mp4", size_bytes=1),
        JobCreate(source=JobSource(type="upload", value="upload-1")),
        ClipUpdate(start=1, end=2, caption_style_id="clean-pop"),
        ErrorResponse(code="not_found", message="Missing"),
    ]
    for value in values:
        assert type(value).model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: Word(text="bad", start=2, end=1), "end must be greater"),
        (
            lambda: CandidateFeatures(
                speech_rate_wps=1,
                audio_energy=2,
                question_count=0,
                scene_changes=0,
                hook_score=0,
            ),
            "less than or equal",
        ),
        (lambda: JobOptions(min_clip_seconds=60, max_clip_seconds=20), "max_clip_seconds"),
        (lambda: ClipUpdate(start=2, end=1), "end must be greater"),
        (lambda: CaptionBackground(color="blue", padding=1, radius=1), "pattern"),
        (lambda: JobSource(type="url", value="file:///tmp/video.mp4"), "HTTP"),
    ],
)
def test_contract_validation_rejects_invalid_values(factory: object, message: str) -> None:
    """Core bounds and relationships are enforced at the model boundary."""
    with pytest.raises(ValidationError, match=message):
        factory()  # type: ignore[operator]
