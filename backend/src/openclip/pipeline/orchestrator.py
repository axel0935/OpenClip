"""Pipeline orchestrator: chains all stages with weights, caching and resume."""

import json
import logging
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from openclip.analyze import analyze as run_analyze
from openclip.analyze.language import check_transcript_language
from openclip.analyze.service import llm_skip_reason
from openclip.captions.ass import build_ass
from openclip.captions.presets import load_presets
from openclip.core.config import Settings
from openclip.core.errors import ConfigError, OpenClipError
from openclip.core.models import (
    Clip,
    CropKeyframe,
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
from openclip.transcribe.cache import load_cached_transcript

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
    "asr_unavailable": (
        'Install the asr extra, e.g. pip install "openclip[asr]"; run `openclip doctor`.'
    ),
    "vision_unavailable": (
        'Install the vision extra, e.g. pip install "openclip[vision]"; run `openclip doctor`.'
    ),
    "model_download_failed": (
        "Check your connection or place the model manually; run `openclip doctor`."
    ),
}


class Cancelled(OpenClipError):
    """A stage stopped because the user cancelled the job."""

    code = "cancelled"


class Pipeline:
    """Owns the job queue, event bus and stage execution for one process."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.bus = EventBus()
        self.queue = JobQueue(self._run_guarded)
        self._rerender_locks: dict[tuple[str, str], threading.Lock] = {}
        self._rerender_locks_guard = threading.Lock()
        # Non-fatal per-run notes (non-English source, low-memory LLM skip).
        self.language_warning: str | None = None
        self.llm_skip_reason: str | None = None
        # Jobs deleted while their worker still runs must not be resurrected.
        self._deleted_jobs: set[str] = set()
        self._deleted_guard = threading.Lock()
        store.mark_interrupted(self.settings)

    def clip_rerender_lock(self, job_id: str, clip_id: str) -> threading.Lock:
        """Return the per-clip re-render lock (callers acquire non-blocking)."""
        key = (job_id, clip_id)
        with self._rerender_locks_guard:
            return self._rerender_locks.setdefault(key, threading.Lock())

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
        """Cancel and delete a job plus its files.

        A job that is still running would otherwise re-insert its own row from
        its next checkpoint, so the id is remembered and every later write for
        it is dropped.
        """
        self.cancel(job_id)
        with self._deleted_guard:
            self._deleted_jobs.add(job_id)
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
        prev_start: float | None = None,
        prev_end: float | None = None,
    ) -> Clip:
        """Re-run captions + render for one clip after a PATCH.

        All artifacts are rendered into a temporary directory and moved into
        place only on success, so a failed re-render keeps the previous files.
        Publishes ``clip_ready`` on success and a clip-scoped ``error`` event
        on failure.

        ``prev_start``/``prev_end`` are the clip bounds *before* the PATCH was
        applied (the API persists the new bounds before this runs in the
        background); they are needed to retime the crop trajectory.
        """
        job = store.load_job(self.settings, job_id)
        if job is None:
            raise OpenClipError(f"Job '{job_id}' not found.")
        clip = next((c for c in job.clips if c.id == clip_id), None)
        if clip is None:
            raise OpenClipError(f"Clip '{clip_id}' not found in job '{job_id}'.")
        old_start = prev_start if prev_start is not None else clip.start
        old_end = prev_end if prev_end is not None else clip.end
        if start is not None:
            clip.start = start
        if end is not None:
            clip.end = end
        workdir = store.job_dir(self.settings, job_id)
        media = MediaInfo.model_validate_json((workdir / "media.json").read_text())
        transcript = load_cached_transcript(workdir)
        if transcript is None:
            raise OpenClipError("Cached transcript is missing or unreadable.")
        crop_file = workdir / f"crop-{clip_id}.json"
        if crop_file.is_file():
            crop = _retime_crop_path(
                CropPath.model_validate_json(crop_file.read_text()),
                old_start,
                old_end,
                clip.start,
                clip.end,
                media,
            )
        else:
            crop = _fit_crop(media)
        crop_file.write_text(crop.model_dump_json(indent=2))
        style_id = style_id or job.options.caption_style_id
        presets = load_presets()
        if style_id not in presets:
            raise OpenClipError(f"Unknown caption style '{style_id}'.")
        style = presets[style_id]

        clip_dir = workdir / "clips" / clip_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = clip_dir / f".rerender-{uuid.uuid4().hex[:8]}"
        tmp_dir.mkdir()
        try:
            _render_clip_artifacts(
                job, clip, media, transcript, crop, style, tmp_dir, self.settings
            )
            for name in ("clip.mp4", "preview.mp4", "thumbnail.jpg", "captions.ass"):
                (tmp_dir / name).replace(clip_dir / name)
        except Exception as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.bus.publish(
                job_id,
                "error",
                {
                    "job_id": job_id,
                    "clip_id": clip_id,
                    "code": getattr(exc, "code", "render_failed"),
                    "message": str(exc),
                },
            )
            raise
        shutil.rmtree(tmp_dir, ignore_errors=True)
        clip.output_file = str(clip_dir / "clip.mp4")
        clip.thumbnail = str(clip_dir / "thumbnail.jpg")
        store.save_job(self.settings, job)
        self.bus.publish(
            job_id,
            "clip_ready",
            {"job_id": job_id, "clip": clip.model_dump(mode="json")},
        )
        return clip

    # -- runner -------------------------------------------------------------

    def execute(self, job: Job, cancel: threading.Event | None = None) -> Job:
        """Run a job synchronously in the calling thread (CLI use)."""
        store.save_job(self.settings, job)
        try:
            self._run(job, cancel or threading.Event())
        except OpenClipError as exc:
            self._finish_failure(job, exc, cancel)
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

    def _finish_failure(
        self, job: Job, exc: OpenClipError, cancel: threading.Event | None = None
    ) -> None:
        """Persist a terminal state for a failed or cancelled run.

        A cancellation is not a failure: the contract has a dedicated
        ``cancelled`` status, and the UI renders it as a user action rather than
        as an error banner. Cancellation is detected from the error code *or*
        from the request flag, because several stages raise a plain
        :class:`OpenClipError` when they observe it.
        """
        code = getattr(exc, "code", "pipeline_failed")
        cancelled = code == "cancelled" or (cancel is not None and cancel.is_set())
        if cancelled:
            job.message = str(exc)
            self._finish(job, JobStatus.CANCELLED, None)
            return
        hint = _EXTRA_HINTS.get(code)
        message = f"{exc} ({hint})" if hint else str(exc)
        self._finish(job, JobStatus.FAILED, JobError(code=code, message=message))

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
            self._finish_failure(job, exc, cancel)
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
        self.language_warning = None
        self.llm_skip_reason = None
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
                self.bus.publish(
                    job.id,
                    "clip_ready",
                    {"job_id": job.id, "clip": clip.model_dump(mode="json")},
                )
                self._save(job)
            except OpenClipError as exc:
                # A cancelled clip is not a render failure: stop the whole job
                # and let the runner persist ``cancelled``.
                if getattr(exc, "code", "") == "cancelled" or cancel.is_set():
                    raise
                code = getattr(exc, "code", "render_failed")
                clip_errors.append(f"{clip.id} ({code}): {exc}")
                log.warning("clip %s failed (%s): %s", clip.id, code, exc)
                self._save(job)

        _check_cancel(cancel)
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
        notes: list[str] = []
        if clip_errors:
            notes.append("Clip failures: " + "; ".join(clip_errors))
        if self.language_warning:
            notes.append(self.language_warning)
        if self.llm_skip_reason:
            notes.append(self.llm_skip_reason)
        job.message = " ".join(notes) if notes else None
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
        # Non-blocking language safeguard: the run continues, but the job message
        # explains why the result may be wrong (English-only v0.1 profiles).
        warning = check_transcript_language(transcript).warning
        self.language_warning = warning
        message = f"Transcription complete. {warning}" if warning else "Transcription complete"
        self._advance(job, Stage.TRANSCRIBE, 1.0, message)
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
        # Keep the resource-guard reason visible after the stage message resets:
        # it is re-attached to the final job message alongside any language note.
        self.llm_skip_reason = llm_skip_reason(job.options)
        if self.llm_skip_reason is not None:
            job.message = self.llm_skip_reason
            self._save(job)
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
        with self._deleted_guard:
            if job.id in self._deleted_jobs:
                return
        job.updated_at = store.utcnow()
        store.save_job(self.settings, job)

    def _finish(self, job: Job, status: JobStatus, error: JobError | None) -> None:
        job.status = status
        job.error = error
        job.progress = 1.0 if status == JobStatus.COMPLETED else job.progress
        self._save(job)
        # ``done`` always terminates the SSE stream and carries the final
        # status; only a job-level ``error`` closes it on failure.
        if status == JobStatus.FAILED:
            self.bus.publish(
                job.id,
                "error",
                {
                    "job_id": job.id,
                    "code": error.code if error else "job_failed",
                    "message": error.message if error else "Job failed.",
                },
            )
        else:
            self.bus.publish(job.id, "done", _job_event(job))


def _order(stage: Stage) -> int:
    return list(STAGE_WEIGHTS).index(stage)


def _sub(progress: ProgressFn, base: float, span: float) -> ProgressFn:
    """Return a progress callback remapped into [base, base+span]."""

    def report(frac: float, msg: str) -> None:
        progress(base + span * max(0.0, min(1.0, frac)), msg)

    return report


def _job_event(job: Job) -> dict:
    return {
        "job_id": job.id,
        "status": str(job.status),
        "stage": str(job.stage) if job.stage else None,
        "progress": job.progress,
        "message": job.message,
    }


def _fit_crop(media: MediaInfo) -> CropPath:
    """Centered ``fit`` fallback crop path (no detector needed)."""
    return CropPath(
        layout="fit",
        source_width=media.width,
        source_height=media.height,
        keyframes=[CropKeyframe(t=0.0, x=0, y=0, w=media.width, h=media.height)],
    )


def _retime_crop_path(
    crop: CropPath,
    old_start: float,
    old_end: float,
    new_start: float,
    new_end: float,
    media: MediaInfo,
) -> CropPath:
    """Shift a ``fill`` crop trajectory onto a trimmed clip range.

    Keyframe ``t`` values are clip-relative, so trimming the head moves the
    whole trajectory by ``old_start - new_start``; values are clamped into
    the new range. Ranges reaching outside the originally tracked window
    (beyond a small tolerance) fall back to a centered ``fit`` path.
    """
    duration = max(0.1, new_end - new_start)
    if crop.layout == "fit":
        return crop
    if new_start < old_start - 0.5 or new_end > old_end + 0.5:
        return _fit_crop(media)
    shift = old_start - new_start
    clamped = sorted(
        (
            CropKeyframe(
                t=min(max(kf.t + shift, 0.0), duration),
                x=kf.x,
                y=kf.y,
                w=kf.w,
                h=kf.h,
            )
            for kf in crop.keyframes
        ),
        key=lambda k: k.t,
    )
    deduped: list[CropKeyframe] = []
    for kf in clamped:
        if deduped and abs(kf.t - deduped[-1].t) < 1e-6:
            deduped[-1] = kf
        else:
            deduped.append(kf)
    if deduped[0].t > 0.0:
        deduped.insert(0, deduped[0].model_copy(update={"t": 0.0}))
    if deduped[-1].t < duration:
        deduped.append(deduped[-1].model_copy(update={"t": duration}))
    return CropPath(
        layout="fill",
        source_width=crop.source_width,
        source_height=crop.source_height,
        keyframes=deduped,
    )


def _render_clip_artifacts(
    job: Job,
    clip: Clip,
    media: MediaInfo,
    transcript: Transcript,
    crop: CropPath,
    style,
    clip_dir: Path,
    settings: Settings,
) -> None:
    """Write captions.ass + clip.mp4 + preview.mp4 + thumbnail.jpg into clip_dir."""
    # Heavy extras are imported lazily so the base install stays importable.
    try:
        from openclip.render import render_clip, render_preview, render_thumbnail  # noqa: PLC0415
    except ImportError as exc:
        raise ConfigError(
            'Reframing/rendering requires the vision extra: pip install "openclip[vision]" '
            "(see `openclip doctor`)"
        ) from exc

    ass_path = clip_dir / "captions.ass"
    ass_path.write_text(build_ass(transcript, clip, style))
    render_clip(
        media,
        clip,
        crop,
        ass_path,
        clip_dir / "clip.mp4",
        job.options,
        settings=settings,
    )
    render_preview(
        media, clip, crop, clip_dir / "preview.mp4", ass_path=ass_path, settings=settings
    )
    render_thumbnail(media, clip, clip_dir / "thumbnail.jpg", settings=settings)


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise Cancelled("Job cancelled.")


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
            'Reframing/rendering requires the vision extra: pip install "openclip[vision]" '
            "(see `openclip doctor`)"
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
        render_preview(
            media,
            clip,
            crop,
            preview_path,
            ass_path=ass_path,
            settings=settings,
            cancel=cancel,
        )
    if not thumb_path.is_file():
        render_thumbnail(media, clip, thumb_path, settings=settings, cancel=cancel)

    clip.output_file = str(out_path)
    clip.thumbnail = str(thumb_path)
