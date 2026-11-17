"""FastAPI application factory implementing the OpenClip HTTP contract."""

import email.utils
import json
import logging
import queue
import re
import shutil
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, TypeAdapter
from starlette.background import BackgroundTask

from openclip import __version__
from openclip.core.config import Settings
from openclip.core.errors import ConfigError, OpenClipError
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
from openclip.ingest.urlcheck import validate_download_url
from openclip.pipeline import store
from openclip.pipeline.orchestrator import Pipeline

log = logging.getLogger(__name__)

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_CHUNK = 1024 * 1024


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=code, message=message).model_dump(),
    )


def _not_found(what: str) -> JSONResponse:
    return _error(status.HTTP_404_NOT_FOUND, "not_found", f"{what} not found.")


def _clip_paths(job_id: str, clip_id: str, settings: Settings) -> dict[str, Path]:
    """Resolve artifact paths for a clip inside its job directory."""
    clip_dir = store.job_dir(settings, job_id) / "clips" / clip_id
    return {
        "video": clip_dir / "clip.mp4",
        "preview": clip_dir / "preview.mp4",
        "thumbnail": clip_dir / "thumbnail.jpg",
        "ass": clip_dir / "captions.ass",
    }


def _safe_file(path: Path, root: Path) -> Path | None:
    """Return ``path`` resolved inside ``root``, or ``None`` on traversal."""
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _is_terminal_frame(frame: str) -> bool:
    """True when an SSE frame ends the job stream.

    ``done`` always terminates; ``error`` terminates only for job-level
    failures — clip-scoped re-render errors carry a ``clip_id`` and must not
    close the stream.
    """
    if frame.startswith("event: done"):
        return True
    if not frame.startswith("event: error"):
        return False
    try:
        data = json.loads(frame.split("data:", 1)[1])
    except (ValueError, IndexError):
        return True
    return "clip_id" not in data


def _file_response(path: Path, media_type: str, request: Request) -> Response:
    """Serve a file with Range support and revalidation-friendly cache headers."""
    stat = path.stat()
    size = stat.st_size
    etag = f'W/"{stat.st_mtime_ns:x}-{size:x}"'
    cache_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
        "ETag": etag,
        "Last-Modified": email.utils.formatdate(stat.st_mtime, usegmt=True),
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)
    range_header = request.headers.get("range")
    if range_header and media_type.startswith("video/"):
        match = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
        if match:
            start_s, end_s = match.groups()
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            end = min(end, size - 1)
            if 0 <= start <= end < size:
                length = end - start + 1

                def body() -> Iterator[bytes]:
                    with path.open("rb") as handle:
                        handle.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = handle.read(min(_CHUNK, remaining))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            yield chunk

                return StreamingResponse(
                    body(),
                    status_code=status.HTTP_206_PARTIAL_CONTENT,
                    media_type=media_type,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{size}",
                        **cache_headers,
                        "Content-Length": str(length),
                    },
                )
    return FileResponse(path, media_type=media_type, headers=cache_headers)


def create_app(
    settings: Settings | None = None,
    web_dir: Path | None = None,
    pipeline: Pipeline | None = None,
) -> FastAPI:
    """Build the local-only OpenClip HTTP application."""
    cfg = settings or Settings()
    pipeline = pipeline or Pipeline(cfg)
    app = FastAPI(title="OpenClip API", version=__version__)
    app.state.pipeline = pipeline
    app.state.settings = cfg
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(OpenClipError)
    async def clip_error_handler(_: Request, exc: OpenClipError) -> JSONResponse:
        code = getattr(exc, "code", "internal_error")
        return _error(status.HTTP_400_BAD_REQUEST, code, str(exc))

    @app.exception_handler(ConfigError)
    async def config_error_handler(_: Request, exc: ConfigError) -> JSONResponse:
        return _error(status.HTTP_400_BAD_REQUEST, "config_error", str(exc))

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        """Report local service liveness."""
        return HealthResponse(status="ok", version=__version__)

    @app.get("/api/caption-styles", response_model=list[CaptionStyle], tags=["caption-styles"])
    def list_caption_styles() -> list[CaptionStyle]:
        """List available caption style presets."""
        from openclip.captions.presets import load_presets

        return sorted(load_presets().values(), key=lambda s: s.id)

    @app.post(
        "/api/uploads",
        response_model=UploadResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["uploads"],
    )
    async def create_upload(file: UploadFile) -> UploadResponse | JSONResponse:
        """Stream an uploaded file to disk under the data dir."""
        filename = Path(file.filename or "upload.bin").name
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "upload.bin"
        upload_id = store.new_upload_id()
        target_dir = store.upload_dir(cfg, upload_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        limit = cfg.max_upload_mb * 1024 * 1024
        size = 0
        try:
            with target.open("wb") as out:
                while chunk := await file.read(_CHUNK):
                    size += len(chunk)
                    if size > limit:
                        out.close()
                        target.unlink(missing_ok=True)
                        shutil.rmtree(target_dir, ignore_errors=True)
                        return _error(
                            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            "too_large",
                            f"Upload exceeds the {cfg.max_upload_mb} MB limit.",
                        )
                    out.write(chunk)
        except OSError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "io_error", str(exc))
        store.save_upload(cfg, upload_id, safe_name, target, size)
        return UploadResponse(upload_id=upload_id, filename=safe_name, size_bytes=size)

    def _resolve_source(source: JobSource) -> JobSource:
        """Validate a JobCreate source into a concrete ingest source."""
        if source.type == "url":
            try:
                validate_download_url(source.value)
            except OpenClipError as exc:
                raise OpenClipError(f"Invalid source URL: {exc}") from exc
            return source
        # upload: value is the upload_id
        row = store.get_upload(cfg, source.value)
        if row is None:
            raise OpenClipError(f"Unknown upload id '{source.value}'.")
        return JobSource(type="upload", value=row.path)

    @app.post(
        "/api/jobs",
        response_model=Job,
        status_code=status.HTTP_201_CREATED,
        tags=["jobs"],
    )
    def create_job(body: JobCreate) -> Job | JSONResponse:
        """Create and enqueue a clipping job."""
        try:
            resolved = _resolve_source(body.source)
        except OpenClipError as exc:
            return _error(
                status.HTTP_400_BAD_REQUEST, getattr(exc, "code", "invalid_source"), str(exc)
            )
        job = Job(
            id=store.new_job_id(),
            status=JobStatus.QUEUED,
            progress=0.0,
            source=resolved,
            options=body.options,
            created_at=store.utcnow(),
            updated_at=store.utcnow(),
        )
        pipeline.submit(job)
        return job

    @app.get("/api/jobs", response_model=list[Job], tags=["jobs"])
    def list_jobs() -> list[Job]:
        """List all jobs, newest first."""
        return pipeline.jobs()

    def _get_job_or_404(job_id: str) -> Job | JSONResponse:
        if not _JOB_ID_RE.match(job_id):
            return _not_found("Job")
        job = pipeline.job(job_id)
        return job if job is not None else _not_found("Job")

    @app.get("/api/jobs/{job_id}", response_model=Job, tags=["jobs"])
    def get_job(job_id: str) -> Job | JSONResponse:
        """Retrieve a job by id."""
        result = _get_job_or_404(job_id)
        return result

    @app.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["jobs"])
    def delete_job(job_id: str) -> Response:
        """Cancel a running job and delete its files."""
        result = _get_job_or_404(job_id)
        if isinstance(result, JSONResponse):
            return result
        pipeline.delete(job_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/api/jobs/{job_id}/events",
        response_class=StreamingResponse,
        response_model=None,
        tags=["jobs"],
    )
    def job_events(job_id: str) -> StreamingResponse | JSONResponse:
        """Server-sent events stream for job progress."""
        result = _get_job_or_404(job_id)
        if isinstance(result, JSONResponse):
            return result
        q = pipeline.bus.subscribe(job_id)

        def stream() -> Iterator[str]:
            # Snapshot first so late subscribers see current state immediately.
            job = pipeline.job(job_id)
            if job is not None:
                snapshot = {
                    "job_id": job.id,
                    "status": str(job.status),
                    "stage": str(job.stage) if job.stage else None,
                    "progress": job.progress,
                    "message": job.message,
                }
                yield f"event: progress\ndata: {json.dumps(snapshot)}\n\n"
            try:
                while True:
                    try:
                        frame = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield frame
                    if _is_terminal_frame(frame):
                        return
            finally:
                pipeline.bus.unsubscribe(job_id, q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.patch(
        "/api/jobs/{job_id}/clips/{clip_id}",
        response_model=Clip,
        tags=["clips"],
    )
    def update_clip(job_id: str, clip_id: str, body: ClipUpdate) -> Clip | JSONResponse:
        """Trim a clip and/or change its caption style; re-renders in background."""
        result = _get_job_or_404(job_id)
        if isinstance(result, JSONResponse):
            return result
        job = result
        clip = next((c for c in job.clips if c.id == clip_id), None)
        if clip is None:
            return _not_found("Clip")

        # Validate the merged range before touching anything.
        new_start = body.start if body.start is not None else clip.start
        new_end = body.end if body.end is not None else clip.end
        if new_end <= new_start:
            return _error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_range",
                "Clip end must be after its start.",
            )
        length = new_end - new_start
        opts = job.options
        if length < opts.min_clip_seconds * 0.9 or length > opts.max_clip_seconds * 1.1:
            return _error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_duration",
                f"Clip length must be within {opts.min_clip_seconds:g}-"
                f"{opts.max_clip_seconds:g} seconds (got {length:.1f}).",
            )
        if job.media is not None and (new_start < 0 or new_end > job.media.duration + 0.5):
            return _error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "out_of_range",
                "Clip bounds must lie inside the source video.",
            )
        if body.caption_style_id is not None:
            from openclip.captions.presets import load_presets

            if body.caption_style_id not in load_presets():
                return _error(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "unknown_style",
                    f"Unknown caption style '{body.caption_style_id}'.",
                )

        lock = pipeline.clip_rerender_lock(job_id, clip_id)
        if not lock.acquire(blocking=False):
            return _error(
                status.HTTP_409_CONFLICT,
                "rerender_in_progress",
                "A re-render for this clip is already running.",
            )

        prev_start, prev_end = clip.start, clip.end
        if body.start is not None:
            clip.start = body.start
        if body.end is not None:
            clip.end = body.end
        if body.caption_style_id is not None:
            job.options.caption_style_id = body.caption_style_id
        store.save_job(cfg, job)

        def bg() -> None:
            try:
                pipeline.rerender_clip(
                    job_id,
                    clip_id,
                    body.start,
                    body.end,
                    body.caption_style_id,
                    prev_start=prev_start,
                    prev_end=prev_end,
                )
            except Exception:
                log.exception("clip re-render failed for %s/%s", job_id, clip_id)
            finally:
                lock.release()

        threading.Thread(target=bg, daemon=True).start()
        return clip

    def _clip_artifact(
        job_id: str, clip_id: str, name: str, media_type: str, request: Request
    ) -> Response | JSONResponse:
        result = _get_job_or_404(job_id)
        if isinstance(result, JSONResponse):
            return result
        job = result
        clip = next((c for c in job.clips if c.id == clip_id), None)
        if clip is None:
            return _not_found("Clip")
        root = store.job_dir(cfg, job_id)
        path = _safe_file(_clip_paths(job_id, clip_id, cfg)[name], root)
        if path is None:
            return _not_found(name.capitalize())
        return _file_response(path, media_type, request)

    @app.get(
        "/api/jobs/{job_id}/clips/{clip_id}/video",
        response_model=None,
        tags=["clips"],
    )
    def clip_video(job_id: str, clip_id: str, request: Request) -> Response:
        """Serve the rendered MP4 with Range support."""
        return _clip_artifact(job_id, clip_id, "video", "video/mp4", request)

    @app.get(
        "/api/jobs/{job_id}/clips/{clip_id}/thumbnail",
        response_model=None,
        tags=["clips"],
    )
    def clip_thumbnail(job_id: str, clip_id: str, request: Request) -> Response:
        """Serve the JPEG thumbnail."""
        return _clip_artifact(job_id, clip_id, "thumbnail", "image/jpeg", request)

    @app.get(
        "/api/jobs/{job_id}/clips/{clip_id}/preview",
        response_model=None,
        tags=["clips"],
    )
    def clip_preview(job_id: str, clip_id: str, request: Request) -> Response:
        """Serve the low-res proxy preview with burned-in captions."""
        return _clip_artifact(job_id, clip_id, "preview", "video/mp4", request)

    @app.get(
        "/api/jobs/{job_id}/clips/{clip_id}/captions.ass",
        response_model=None,
        tags=["clips"],
    )
    def clip_captions(job_id: str, clip_id: str, request: Request) -> Response:
        """Serve the generated ASS subtitle file."""
        return _clip_artifact(job_id, clip_id, "ass", "text/plain; charset=utf-8", request)

    @app.get(
        "/api/jobs/{job_id}/download.zip",
        response_model=None,
        tags=["jobs"],
    )
    def download_job(job_id: str) -> Response:
        """Stream a ZIP of all rendered clips, thumbnails and captions."""
        result = _get_job_or_404(job_id)
        if isinstance(result, JSONResponse):
            return result
        job = result
        if job.status != JobStatus.COMPLETED:
            return _error(status.HTTP_409_CONFLICT, "not_ready", "Job has not completed.")
        root = store.job_dir(cfg, job_id)
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp.close()
        try:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
                for clip in job.clips:
                    paths = _clip_paths(job_id, clip.id, cfg)
                    for key, arc in (
                        ("video", "clip.mp4"),
                        ("thumbnail", "thumbnail.jpg"),
                        ("ass", "captions.ass"),
                        ("preview", "preview.mp4"),
                    ):
                        p = _safe_file(paths[key], root)
                        if p is not None:
                            zf.write(p, f"{clip.id}/{arc}")
                results = root / "results.json"
                if results.is_file():
                    zf.write(results, "results.json")
        except OSError as exc:
            Path(tmp.name).unlink(missing_ok=True)
            return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "io_error", str(exc))
        return FileResponse(
            tmp.name,
            media_type="application/zip",
            filename=f"openclip-{job_id}.zip",
            background=BackgroundTask(Path(tmp.name).unlink, missing_ok=True),
        )

    # Optional SPA static hosting (added last so /api wins).
    if web_dir is not None and Path(web_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    def custom_openapi() -> dict[str, Any]:
        """Include every shared contract, including future-stage-only models."""
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        models: tuple[type[BaseModel], ...] = (
            Word,
            Segment,
            Transcript,
            MediaInfo,
            CandidateFeatures,
            Candidate,
            Clip,
            CropKeyframe,
            CropPath,
            CaptionBackground,
            CaptionStyle,
            JobSource,
            JobOptions,
            JobError,
            Job,
            HealthResponse,
            UploadResponse,
            JobCreate,
            ClipUpdate,
            ErrorResponse,
        )
        for model in models:
            schemas.setdefault(
                model.__name__, model.model_json_schema(ref_template="#/components/schemas/{model}")
            )
        for enum in (ClipSource, Stage, JobStatus):
            schemas.setdefault(enum.__name__, TypeAdapter(enum).json_schema())
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
    return app


app = create_app()
