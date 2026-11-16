"""Pipeline orchestrator: chains all stages with weights, caching and resume."""

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from openclip.analyze import analyze as run_analyze
from openclip.captions.ass import build_ass
from openclip.captions.presets import load_presets
from openclip.core.config import Settings
from openclip.core.errors import ConfigError, OpenClipError
from openclip.core.models import (
    Clip,
    CropPath,
    Job,
    JobError,
    JobStatus,
    MediaInfo,
    Stage,
    Transcript,
)
from openclip.ingest import ingest as run_ingest
from openclip.pipeline import store
from openclip.pipeline.queue import EventBus, JobQueue
from openclip.transcribe import transcribe as run_transcribe

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

# Overall progress weights per stage (documented in docs/ARCHITECTURE.md).
STAGE_WEIGHTS: dict[Stage, float] = {
    Stage.INGEST: 0.10,
    Stage.TRANSCRIBE: 0.25,
    Stage.SEGMENT: 0.05,
    Stage.SCORE: 0.05,
    Stage.SELECT: 0.05,
    Stage.REFRAME: 0.10,
    Stage.CAPTIONS: 0.05,
    Stage.RENDER: 0.30,
    Stage.EXPORT: 0.05,
}

_ANALYZE_WEIGHTS = {
    Stage.SEGMENT: STAGE_WEIGHTS[Stage.SEGMENT],
    Stage.SCORE: STAGE_WEIGHTS[Stage.SCORE],
    Stage.SELECT: STAGE_WEIGHTS[Stage.SELECT],
}

_EXTRA_HINTS = {
    "asr_unavailable": "Install the ASR extra: uv sync --extra asr",
    "vision_unavailable": "Install the vision extra: uv sync --extra vision",
    "model_download_failed": "Check your connection or place the model manually.",
}


class Pipeline:
    """Owns the job queue, event bus and stage execution for one process."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.bus = EventBus()
        self.queue = JobQueue(self._run_guarded)
        store.mark_interrupted(self.settings)

    # -- public API ---------------------------------------------------------

    def submit(self, job: Job) -> None:
        """Persist and enqueue a new job."""
        store.save_job(self.settings, job)
        self.queue.submit(job.id)

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued/running job."""
        return self.queue.cancel(job_id)

    def job(self, job_id: str) -> Job | None:
        """Load a job from the store."""
        return store.load_job(self.settings, job_id)

    def jobs(self) -> list[Job]:
        """List all jobs."""
        return store.list_jobs(self.settings)

    def delete(self, job_id: str) -> bool:
        """Cancel and delete a job plus its files."""
        self.cancel(job_id)
        existed = store.delete_job_row(self.settings, job_id)
        store.delete_job_files(self.settings, job_id)
        return existed

    def rerender_clip(
        self,
        job_id: str,
        clip_id: str,
        start: float | None,
        end: float | None,
        style_id: str | None,
    ) -> Clip:
        """Re-run captions + render for one clip after a PATCH (background)."""
        job = store.load_job(self.settings, job_id)
        if job is None:
            raise OpenClipError(f"Job '{job_id}' not found.")
        clip = next((c for c in job.clips if c.id == clip_id), None)
        if clip is None:
            raise OpenClipError(f"Clip '{clip_id}' not found in job '{job_id}'.")
        if start is not None:
            clip.start = start
        if end is not None:
            clip.end = end
        workdir = store.job_dir(self.settings, job_id)
        media = MediaInfo.model_validate_json((workdir / "media.json").read_text())
        transcript = Transcript.model_validate_json((workdir / "transcript.json").read_text())
        crop = CropPath.model_validate_json((workdir / f"crop-{clip_id}.json").read_text())
        style_id = style_id or job.options.caption_style_id
        style = load_presets()[style_id]
        _render_one_clip(
            job, clip, media, transcript, crop, style, workdir, self.settings, self.bus, cancel=None
        )
        store.save_job(self.settings, job)
        return clip

    # -- runner -------------------------------------------------------------

    def execute(self, job: Job, cancel: threading.Event | None = None) -> Job:
        """Run a job synchronously in the calling thread (CLI use)."""
        store.save_job(self.settings, job)
        try:
            self._run(job, cancel or threading.Event())
        except OpenClipError as exc:
            code = getattr(exc, "code", "pipeline_failed")
            hint = _EXTRA_HINTS.get(code)
            message = f"{exc} ({hint})" if hint else str(exc)
            self._finish(job, JobStatus.FAILED, JobError(code=code, message=message))
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", job.id)
            self._finish(
                job,
                JobStatus.FAILED,
                JobError(code="internal_error", message=f"Unexpected error: {exc}"),
            )
            raise
        return store.load_job(self.settings, job.id) or job

    def _run_guarded(self, job_id: str) -> None:
        """Queue entry point: run the pipeline and always persist terminal state."""
        job = store.load_job(self.settings, job_id)
        if job is None:
            return
        handle = self.queue.handle(job_id)
        cancel = handle.cancel if handle else threading.Event()
        try:
            self._run(job, cancel)
        except OpenClipError as exc:
            code = getattr(exc, "code", "pipeline_failed")
            hint = _EXTRA_HINTS.get(code)
            message = f"{exc} ({hint})" if hint else str(exc)
            self._finish(job, JobStatus.FAILED, JobError(code=code, message=message))
        except Exception as exc:  # noqa: BLE001 - jobs must never kill the worker
            log.exception("job %s failed", job_id)
            self._finish(
                job,
                JobStatus.FAILED,
                JobError(code="internal_error", message=f"Unexpected error: {exc}"),
            )

    def _run(self, job: Job, cancel: threading.Event) -> None:
        settings = self.settings
        workdir = store.job_dir(settings, job.id)
        job.status = JobStatus.RUNNING
        job.error = None
        self._save(job)

        progress = self._make_progress(job)
        self._stage_box = {"stage": Stage.INGEST}
        media = self._stage_ingest(job, workdir, progress, cancel)
        transcript = self._stage_transcribe(job, media, workdir, progress, cancel)
        clips = self._stage_analyze(job, transcript, media, workdir, progress, cancel)
        job.clips = clips
        self._save(job)

        presets = load_presets()
        style = presets[job.options.caption_style_id]
        clip_errors: list[str] = []
        render_base = sum(
            STAGE_WEIGHTS[s] for s in STAGE_WEIGHTS if _order(s) < _order(Stage.RENDER)
        )
        render_span = STAGE_WEIGHTS[Stage.RENDER] / max(1, len(clips))
        for index, clip in enumerate(clips):
            _check_cancel(cancel)
            self._stage_box["stage"] = Stage.RENDER

            def clip_progress(frac: float, msg: str, _i: int = index) -> None:
                job.progress = render_base + render_span * (_i + max(0.0, min(1.0, frac)))
                job.message = msg
                self.bus.publish(job.id, "progress", _job_event(job))

            try:
                _render_one_clip(
                    job,
                    clip,
                    media,
                    transcript,
                    None,
                    style,
                    workdir,
                    settings,
                    self.bus,
                    cancel,
                    progress_fn=clip_progress,
                )
                self.bus.publish(job.id, "clip_ready", {"clip": clip.model_dump(mode="json")})
                self._save(job)
            except OpenClipError as exc:
                clip_errors.append(f"{clip.id}: {exc}")
                log.warning("clip %s failed: %s", clip.id, exc)
                self._save(job)

        self._set_stage(job, Stage.EXPORT)
        results = [
            {
                "id": c.id,
                "title": c.title,
                "score": c.score,
                "start": c.start,
                "end": c.end,
                "output_file": c.output_file,
                "thumbnail": c.thumbnail,
            }
            for c in job.clips
        ]
        (workdir / "results.json").write_text(json.dumps(results, indent=2))
        if clip_errors:
            job.message = "Clip failures: " + "; ".join(clip_errors)
        self._finish(job, JobStatus.COMPLETED, None)

    # -- stages ---------------------------------------------------------------

    def _stage_ingest(self, job, workdir, progress, cancel) -> MediaInfo:
        self._set_stage(job, Stage.INGEST)
        media_file = workdir / "media.json"
        if media_file.is_file():
            media = MediaInfo.model_validate_json(media_file.read_text())
            job.media = media
            self._save(job)
            self._advance(job, Stage.INGEST, 1.0, "Media already ingested (resumed)")
            return media
        media = run_ingest(
            job.source,
            workdir,
            self.settings,
            progress=_sub(progress, 0.0, STAGE_WEIGHTS[Stage.INGEST]),
            cancel=cancel,
        )
        media_file.write_text(media.model_dump_json(indent=2))
        job.media = media
        self._save(job)
        self._advance(job, Stage.INGEST, 1.0, "Ingest complete")
        return media

    def _stage_transcribe(self, job, media, workdir, progress, cancel) -> Transcript:
        self._set_stage(job, Stage.TRANSCRIBE)
        transcript_file = workdir / "transcript.json"
        # transcribe() already caches inside workdir; reuse when present.
        transcript = run_transcribe(
            media,
            job.options,
            workdir=workdir,
            settings=self.settings,
            progress=_sub(progress, STAGE_WEIGHTS[Stage.INGEST], STAGE_WEIGHTS[Stage.TRANSCRIBE]),
            cancel=cancel,
        )
        transcript_file.write_text(json.dumps({"transcript": transcript.model_dump(mode="json")}))
        self._advance(job, Stage.TRANSCRIBE, 1.0, "Transcription complete")
        return transcript

    def _stage_analyze(self, job, transcript, media, workdir, progress, cancel):
        clips_file = workdir / "clips.json"
        if clips_file.is_file():
            clips = [Clip.model_validate(c) for c in json.loads(clips_file.read_text())]
            self._advance(job, Stage.SELECT, 1.0, "Analysis reused (resumed)")
            return clips
        # Split the analyze weight across segment/score/select for observability.
        state = {"stage": Stage.SEGMENT}

        def analyze_progress(frac: float, msg: str) -> None:
            if frac < 0.5:
                stage = Stage.SEGMENT
            elif frac < 0.9:
                stage = Stage.SCORE
            else:
                stage = Stage.SELECT
            if stage != state["stage"]:
                state["stage"] = stage
                job.stage = stage
            weight = _ANALYZE_WEIGHTS[stage]
            inner = (
                frac
                if stage == Stage.SEGMENT
                else (frac - 0.5) / 0.4
                if stage == Stage.SCORE
                else (frac - 0.9) / 0.1
            )
            inner = max(0.0, min(1.0, inner))
            base = STAGE_WEIGHTS[Stage.INGEST] + STAGE_WEIGHTS[Stage.TRANSCRIBE]
            done = sum(
                _ANALYZE_WEIGHTS[s]
                for s in _ANALYZE_WEIGHTS
                if s is not stage and _order(s) < _order(stage)
            )
            job.progress = base + done + weight * inner
            job.message = msg
            self.bus.publish(job.id, "progress", _job_event(job))

        clips = run_analyze(
            transcript,
            media,
            job.options,
            settings=self.settings,
            progress=analyze_progress,
            cancel=cancel,
        )
        clips_file.write_text(json.dumps([c.model_dump(mode="json") for c in clips]))
        self._advance(job, Stage.SELECT, 1.0, f"Selected {len(clips)} clips")
        return clips

    # -- progress plumbing ----------------------------------------------------

    def _make_progress(self, job: Job) -> ProgressFn:
        """Map a 0-1 stage fraction onto the global weighted progress."""

        def report(frac: float, msg: str) -> None:
            stage = getattr(self, "_stage_box", {}).get("stage", Stage.INGEST)
            weight = STAGE_WEIGHTS.get(stage, 0.05)
            base = sum(STAGE_WEIGHTS[s] for s in STAGE_WEIGHTS if _order(s) < _order(stage))
            job.progress = base + weight * max(0.0, min(1.0, frac))
            job.message = msg
            self.bus.publish(job.id, "progress", _job_event(job))

        return report

    def _set_stage(self, job: Job, stage: Stage) -> None:
        job.stage = stage
        if hasattr(self, "_stage_box"):
            self._stage_box["stage"] = stage
        self._save(job)

    def _advance(self, job: Job, stage: Stage, frac: float, msg: str) -> None:
        weight = STAGE_WEIGHTS[stage]
        base = sum(STAGE_WEIGHTS[s] for s in STAGE_WEIGHTS if _order(s) < _order(stage))
        job.progress = base + weight * frac
        job.message = msg
        self.bus.publish(job.id, "progress", _job_event(job))
        self._save(job)

    def _save(self, job: Job) -> None:
        job.updated_at = store.utcnow()
        store.save_job(self.settings, job)

    def _finish(self, job: Job, status: JobStatus, error: JobError | None) -> None:
        job.status = status
        job.error = error
        job.progress = 1.0 if status == JobStatus.COMPLETED else job.progress
        self._save(job)
        event = (
            "done"
            if status == JobStatus.COMPLETED
            else ("error" if status == JobStatus.FAILED else "progress")
        )
        self.bus.publish(job.id, event, _job_event(job))


def _order(stage: Stage) -> int:
    return list(STAGE_WEIGHTS).index(stage)


def _sub(progress: ProgressFn, base: float, span: float) -> ProgressFn:
    """Return a progress callback remapped into [base, base+span]."""

    def report(frac: float, msg: str) -> None:
        progress(base + span * max(0.0, min(1.0, frac)), msg)

    return report


def _job_event(job: Job) -> dict:
    return {
        "id": job.id,
        "status": str(job.status),
        "stage": str(job.stage) if job.stage else None,
        "progress": job.progress,
        "message": job.message,
    }


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise OpenClipError("Job cancelled.")


def _render_one_clip(
    job: Job,
    clip: Clip,
    media: MediaInfo,
    transcript: Transcript,
    crop: CropPath | None,
    style,
    workdir: Path,
    settings: Settings,
    bus: EventBus,
    cancel: threading.Event | None,
    *,
    progress_fn: ProgressFn | None = None,
) -> None:
    """Run reframe -> captions -> render -> thumbnail/preview for one clip."""
    # Heavy extras are imported lazily so the base install stays importable.
    try:
        from openclip.reframe import compute_crop_path  # noqa: PLC0415
        from openclip.render import render_clip, render_preview, render_thumbnail  # noqa: PLC0415
    except ImportError as exc:
        raise ConfigError(
            "Reframing/rendering requires extra dependencies: uv sync --extra vision"
        ) from exc

    clip_dir = workdir / "clips" / clip.id
    clip_dir.mkdir(parents=True, exist_ok=True)

    def clip_progress(frac: float, msg: str) -> None:
        if progress_fn is not None:
            progress_fn(frac, msg)

    # reframe
    crop_file = workdir / f"crop-{clip.id}.json"
    if crop is None and crop_file.is_file():
        crop = CropPath.model_validate_json(crop_file.read_text())
    if crop is None:
        crop = compute_crop_path(media, clip, job.options.layout, settings=settings, cancel=cancel)
        crop_file.write_text(crop.model_dump_json(indent=2))

    # captions
    ass_path = clip_dir / "captions.ass"
    ass_path.write_text(build_ass(transcript, clip, style))

    # render
    out_path = clip_dir / "clip.mp4"
    preview_path = clip_dir / "preview.mp4"
    thumb_path = clip_dir / "thumbnail.jpg"
    if not out_path.is_file():
        render_clip(
            media,
            clip,
            crop,
            ass_path,
            out_path,
            job.options,
            settings=settings,
            progress=clip_progress,
            cancel=cancel,
        )
    if not preview_path.is_file():
        render_preview(media, clip, crop, preview_path, settings=settings, cancel=cancel)
    if not thumb_path.is_file():
        render_thumbnail(media, clip, thumb_path, settings=settings, cancel=cancel)

    clip.output_file = str(out_path)
    clip.thumbnail = str(thumb_path)
