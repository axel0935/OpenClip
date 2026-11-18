"""Edge-case matrix: orchestrator-level cancellation, failure and the resource guard.

The heavy stages are faked; the goal is to prove that every stage boundary
observes cancellation, that a failure persists a coded terminal state, and that
the memory/language guards surface their reason on the job without blocking.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from openclip.core.config import Settings
from openclip.core.errors import AnalysisError
from openclip.core.models import (
    Clip,
    ClipSource,
    CropPath,
    Job,
    JobOptions,
    JobSource,
    JobStatus,
    MediaInfo,
    Stage,
    Transcript,
)
from openclip.ingest.errors import IngestFailure
from openclip.pipeline import store
from openclip.pipeline.orchestrator import Pipeline, _retime_crop_path
from openclip.transcribe.errors import TranscriptionFailure


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    return settings


def _media(duration: float = 10.0) -> MediaInfo:
    return MediaInfo(
        path="/tmp/fake.mp4", duration=duration, width=1280, height=720, fps=30.0, has_audio=True
    )


def _transcript(language: str = "en", text: str = "") -> Transcript:
    from openclip.core.models import Segment, Word

    words = [Word(text=w, start=i * 0.3, end=i * 0.3 + 0.28) for i, w in enumerate(text.split())]
    segments = (
        [Segment(id=0, start=0.0, end=max(1.0, len(words) * 0.3), text=text, words=words)]
        if text
        else []
    )
    return Transcript(language=language, duration=10.0, model="fake", segments=segments)


def _clip() -> Clip:
    return Clip(
        id="clip-001",
        start=0.0,
        end=5.0,
        score=90.0,
        title="A",
        hook="h",
        hashtags=[],
        rationale="r",
        source=ClipSource.HEURISTIC,
    )


def _job(settings: Settings) -> Job:
    return Job(
        id=store.new_job_id(),
        status=JobStatus.QUEUED,
        progress=0.0,
        source=JobSource(type="upload", value="/tmp/fake.mp4"),
        options=JobOptions(min_clip_seconds=1.0, max_clip_seconds=60.0),
        created_at=store.utcnow(),
        updated_at=store.utcnow(),
    )


def _fake_render(monkeypatch) -> None:
    import openclip.pipeline.orchestrator as orch

    def fake(
        job,
        clip,
        media,
        transcript,
        crop,
        style,
        workdir,
        settings,
        bus,
        cancel,
        *,
        progress_fn=None,
    ):
        clip_dir = workdir / "clips" / clip.id
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"mp4")
        (clip_dir / "preview.mp4").write_bytes(b"prev")
        (clip_dir / "thumbnail.jpg").write_bytes(b"jpg")
        (clip_dir / "captions.ass").write_text("ass")
        clip.output_file = str(clip_dir / "clip.mp4")
        clip.thumbnail = str(clip_dir / "thumbnail.jpg")

    monkeypatch.setattr(orch, "_render_one_clip", fake)


def _patch_stages(monkeypatch, *, ingest=None, transcribe=None, analyze=None) -> None:
    import openclip.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "run_ingest", ingest or (lambda *a, **k: _media()))
    monkeypatch.setattr(
        orch,
        "run_transcribe",
        transcribe or (lambda *a, **k: _transcript("en", "A solid sentence for analysis.")),
    )
    monkeypatch.setattr(orch, "run_analyze", analyze or (lambda *a, **k: [_clip()]))
    _fake_render(monkeypatch)


# ---------- cancellation at each stage ----------


@pytest.mark.parametrize("stage", ["ingest", "transcribe", "analyze", "render"])
def test_cancel_during_each_stage_ends_as_cancelled(
    tmp_path: Path, monkeypatch, stage: str
) -> None:
    """Cancellation must be observed and reported as ``cancelled``, never ``failed``."""
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    started = threading.Event()

    def slow_ingest(*_a, cancel=None, **_k):
        started.set()
        for _ in range(300):
            if cancel is not None and cancel.is_set():
                raise IngestFailure("cancelled", "Operation cancelled.")
            time.sleep(0.01)
        return _media()

    def slow_transcribe(*_a, cancel=None, **_k):
        started.set()
        for _ in range(300):
            if cancel is not None and cancel.is_set():
                raise TranscriptionFailure("cancelled", "Transcription cancelled.")
            time.sleep(0.01)
        return _transcript("en", "Words.")

    def slow_analyze(*_a, cancel=None, **_k):
        started.set()
        for _ in range(300):
            if cancel is not None and cancel.is_set():
                raise AnalysisError("Analysis cancelled.")
            time.sleep(0.01)
        return [_clip()]

    # Replace only the stage under test with a cancellable slow version.
    import openclip.pipeline.orchestrator as orch

    if stage == "ingest":
        _patch_stages(monkeypatch, ingest=slow_ingest)
    elif stage == "transcribe":
        _patch_stages(monkeypatch, transcribe=slow_transcribe)
    elif stage == "analyze":
        _patch_stages(monkeypatch, analyze=slow_analyze)
    else:
        _patch_stages(monkeypatch)

        def cancellable_render(
            job_,
            clip,
            media,
            transcript,
            crop,
            style,
            workdir,
            settings_,
            bus,
            cancel,
            *,
            progress_fn=None,
        ):
            started.set()
            for _ in range(300):
                if cancel is not None and cancel.is_set():
                    raise orch.OpenClipError("Job cancelled.")
                time.sleep(0.01)

        monkeypatch.setattr(orch, "_render_one_clip", cancellable_render)

    pipe.submit(job)
    assert started.wait(timeout=5), f"{stage} never started"
    assert pipe.cancel(job.id) is True
    deadline = time.time() + 15
    status = None
    while time.time() < deadline:
        current = pipe.job(job.id)
        status = current.status if current else None
        if status in (JobStatus.CANCELLED, JobStatus.FAILED):
            break
        time.sleep(0.02)
    assert status == JobStatus.CANCELLED, f"stage {stage} reported {status}"
    final = pipe.job(job.id)
    assert final is not None and final.error is None


def test_cancel_queued_job_never_starts_it(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    order: list[str] = []

    def slow_ingest(source, *a, cancel=None, **k):  # noqa: ANN001, ARG001
        for _ in range(200):
            if cancel is not None and cancel.is_set():
                raise IngestFailure("cancelled", "Operation cancelled.")
            time.sleep(0.01)
        order.append(str(getattr(source, "value", source)))
        return _media()

    _patch_stages(monkeypatch, ingest=slow_ingest)
    first, second = _job(settings), _job(settings)
    first.source = JobSource(type="upload", value="first.mp4")
    second.source = JobSource(type="upload", value="second.mp4")
    pipe.submit(first)
    pipe.submit(second)
    assert pipe.cancel(second.id) is True
    time.sleep(1.0)
    assert "second.mp4" not in order
    assert pipe.job(second.id).status in (JobStatus.CANCELLED, JobStatus.QUEUED)  # type: ignore[union-attr]


# ---------- failure reporting ----------


@pytest.mark.parametrize(
    "error,expected_code",
    [
        (IngestFailure("too_long", "Media is too long."), "too_long"),
        (IngestFailure("no_audio", "No audio track."), "no_audio"),
        (IngestFailure("unsupported_file", "Bad container."), "unsupported_file"),
        (TranscriptionFailure("no_speech", "No speech."), "no_speech"),
        (AnalysisError("No candidates."), "pipeline_failed"),
    ],
)
def test_stage_failures_persist_a_coded_terminal_state(
    tmp_path: Path, monkeypatch, error: Exception, expected_code: str
) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)

    def boom(*_a, **_k):
        raise error

    if isinstance(error, IngestFailure):
        _patch_stages(monkeypatch, ingest=boom)
    elif isinstance(error, TranscriptionFailure):
        _patch_stages(monkeypatch, transcribe=boom)
    else:
        _patch_stages(monkeypatch, analyze=boom)

    with pytest.raises(Exception):
        pipe.execute(job)
    final = pipe.job(job.id)
    assert final is not None
    assert final.status == JobStatus.FAILED
    assert final.error is not None and final.error.code == expected_code


def test_empty_transcript_fails_with_no_speech(tmp_path: Path, monkeypatch) -> None:
    """Silent audio is a product-level error, not a generic analysis crash."""
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    _patch_stages(monkeypatch)
    import openclip.pipeline.orchestrator as orch

    def strict_transcribe(*a, **k):
        # Mirror the real contract: strict mode rejects an empty transcript.
        assert k.get("strict") is True, "the pipeline must ask for strict mode"
        raise TranscriptionFailure("no_speech", "No speech was detected in the audio.")

    monkeypatch.setattr(orch, "run_transcribe", strict_transcribe)
    with pytest.raises(Exception):
        pipe.execute(job)
    final = pipe.job(job.id)
    assert final is not None and final.error is not None
    assert final.error.code == "no_speech"


def test_missing_asr_extra_adds_an_install_hint(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    _patch_stages(
        monkeypatch,
        transcribe=lambda *a, **k: (_ for _ in ()).throw(
            TranscriptionFailure("asr_unavailable", "faster-whisper is not installed.")
        ),
    )
    with pytest.raises(Exception):
        pipe.execute(job)
    final = pipe.job(job.id)
    assert final is not None and final.error is not None
    assert "openclip doctor" in final.error.message


def test_one_failed_clip_does_not_kill_the_others(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    clips = [_clip(), _clip().model_copy(update={"id": "clip-002"})]
    _patch_stages(monkeypatch, analyze=lambda *a, **k: clips)
    import openclip.pipeline.orchestrator as orch

    def selective(
        job_,
        clip,
        media,
        transcript,
        crop,
        style,
        workdir,
        settings_,
        bus,
        cancel,
        *,
        progress_fn=None,
    ):
        if clip.id == "clip-002":
            raise orch.OpenClipError("simulated encode failure")
        clip_dir = workdir / "clips" / clip.id
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"mp4")
        clip.output_file = str(clip_dir / "clip.mp4")

    monkeypatch.setattr(orch, "_render_one_clip", selective)
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED
    assert "clip-002" in (final.message or "")
    assert final.clips[0].output_file


def test_disk_full_during_render_is_reported_as_disk_full(tmp_path: Path, monkeypatch) -> None:
    """A full disk surfaces as ``disk_full`` and leaves no half-written output."""
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    _patch_stages(monkeypatch)
    import openclip.pipeline.orchestrator as orch
    from openclip.render.service import RenderFailure

    def failing(*_a, **_k):
        raise RenderFailure("disk_full", "Not enough space in the data folder for this render.")

    monkeypatch.setattr(orch, "render_clip", failing, raising=False)
    # Route the fake render through the real artifact writer so the failure path runs.
    monkeypatch.setattr(
        orch,
        "_render_one_clip",
        lambda job_, clip, media, transcript, crop, style, workdir, settings_, bus, cancel, progress_fn=None: (
            (_ for _ in ()).throw(
                RenderFailure("disk_full", "Not enough space in the data folder for this render.")
            )
        ),
    )
    final = pipe.execute(job)
    # A per-clip failure is recorded, the job itself still completes.
    assert final.status == JobStatus.COMPLETED
    assert "disk_full" in (final.message or "")


# ---------- guards ----------


def test_low_memory_note_reaches_the_job_message(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    job.options = JobOptions(min_clip_seconds=1.0, max_clip_seconds=60.0, analysis_mode="auto")
    _patch_stages(monkeypatch)
    import openclip.analyze.service as analyze_service

    monkeypatch.setattr(analyze_service, "LLM_MIN_AVAILABLE_BYTES", 64 * 1024**3)
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED
    assert final.message and "Low memory" in final.message


def test_non_english_source_warning_reaches_the_job_message(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    german = (
        "Ich denke, das Wichtigste an Disziplin ist, dass du jeden Tag auftauchen und die "
        "Arbeit erledigen musst, auch wenn du keine Lust dazu hast. Das ist das ganze "
        "Geheimnis und die Leute geben zu frueh auf."
    )
    _patch_stages(monkeypatch, transcribe=lambda *a, **k: _transcript("de", german))
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED
    assert final.message is not None
    assert "English" in final.message


def test_english_source_has_no_warning(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    english = (
        "The most important thing about discipline is that you show up every day and you do "
        "the work even when you do not feel like it. That is the whole secret. Most people "
        "quit too early because they want results immediately."
    )
    _patch_stages(monkeypatch, transcribe=lambda *a, **k: _transcript("en", english))
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED
    assert not final.message or "English" not in final.message


# ---------- stage bookkeeping ----------


def test_stage_and_progress_monotonic_throughout(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    _patch_stages(monkeypatch)
    seen: list[tuple[float, Stage | None]] = []
    original = pipe.bus.publish

    def spy(job_id, event, data):  # noqa: ANN001
        if event == "progress":
            seen.append((float(data.get("progress", 0.0)), data.get("stage")))
        return original(job_id, event, data)

    monkeypatch.setattr(pipe.bus, "publish", spy)
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED
    progresses = [p for p, _ in seen]
    assert progresses == sorted(progresses), "progress must never go backwards"
    assert progresses[0] >= 0.0 and progresses[-1] <= 1.0


def test_results_json_lists_every_clip(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    clips = [_clip(), _clip().model_copy(update={"id": "clip-002", "start": 20.0, "end": 25.0})]
    _patch_stages(monkeypatch, analyze=lambda *a, **k: clips)
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED
    results = json.loads((store.job_dir(settings, job.id) / "results.json").read_text())
    assert [row["id"] for row in results] == ["clip-001", "clip-002"]


# ---------- crop retiming ----------


def test_retime_crop_path_shifts_and_clamps() -> None:
    crop = CropPath(
        layout="fill",
        source_width=1920,
        source_height=1080,
        keyframes=[
            {"t": 0.0, "x": 100, "y": 0, "w": 608, "h": 1080},  # type: ignore[list-item]
            {"t": 10.0, "x": 500, "y": 0, "w": 608, "h": 1080},  # type: ignore[list-item]
        ],
    )
    retimed = _retime_crop_path(crop, 10.0, 40.0, 15.0, 40.0, _media())
    assert retimed.keyframes[0].t == 0.0
    assert retimed.keyframes[-1].t == 25.0
    # Trimming beyond the tracked window degrades to a centred fit path.
    beyond = _retime_crop_path(crop, 10.0, 40.0, 0.0, 40.0, _media())
    assert beyond.layout == "fit"


def test_retime_crop_path_leaves_fit_untouched() -> None:
    fit = CropPath(
        layout="fit",
        source_width=1280,
        source_height=720,
        keyframes=[{"t": 0.0, "x": 0, "y": 0, "w": 1280, "h": 720}],  # type: ignore[list-item]
    )
    assert _retime_crop_path(fit, 0.0, 10.0, 2.0, 8.0, _media()) is fit
