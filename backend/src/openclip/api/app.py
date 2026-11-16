"""FastAPI application factory and contract-first endpoint declarations."""

from typing import Any, NoReturn

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, TypeAdapter

from openclip import __version__
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


class StubEndpointError(Exception):
    """Signals an intentionally unimplemented contract endpoint."""


def _not_implemented() -> NoReturn:
    """Raise the uniform scaffold-only endpoint result."""
    raise StubEndpointError


def create_app() -> FastAPI:
    """Build the local-only OpenClip HTTP application."""
    app = FastAPI(title="OpenClip API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(StubEndpointError)
    async def stub_endpoint_error_handler(_: Request, __: StubEndpointError) -> JSONResponse:
        """Return the standard scaffold-only error payload."""
        return JSONResponse(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            content=ErrorResponse(
                code="not_implemented", message="not implemented yet"
            ).model_dump(),
        )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        """Report local service liveness."""
        return HealthResponse(status="ok", version=__version__)

    stub_responses: dict[int | str, dict[str, Any]] = {
        501: {"model": ErrorResponse, "description": "Not implemented yet."}
    }

    @app.get(
        "/api/caption-styles",
        response_model=list[CaptionStyle],
        responses=stub_responses,
        tags=["caption-styles"],
    )
    def list_caption_styles() -> list[CaptionStyle]:
        """List available caption styles when presets are implemented."""
        _not_implemented()

    @app.post(
        "/api/uploads",
        response_model=UploadResponse,
        status_code=status.HTTP_201_CREATED,
        responses=stub_responses,
        tags=["uploads"],
    )
    def create_upload() -> UploadResponse:
        """Accept a local upload when ingestion is implemented."""
        _not_implemented()

    @app.post(
        "/api/jobs",
        response_model=Job,
        status_code=status.HTTP_201_CREATED,
        responses=stub_responses,
        tags=["jobs"],
    )
    def create_job(_: JobCreate) -> Job:
        """Create a pipeline job when persistence is implemented."""
        _not_implemented()

    @app.get("/api/jobs", response_model=list[Job], responses=stub_responses, tags=["jobs"])
    def list_jobs() -> list[Job]:
        """List saved pipeline jobs when persistence is implemented."""
        _not_implemented()

    @app.get("/api/jobs/{job_id}", response_model=Job, responses=stub_responses, tags=["jobs"])
    def get_job(job_id: str) -> Job:
        """Retrieve a job by identifier when persistence is implemented."""
        del job_id
        _not_implemented()

    @app.delete(
        "/api/jobs/{job_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=stub_responses,
        tags=["jobs"],
    )
    def delete_job(job_id: str) -> Response:
        """Cancel or delete a job when persistence is implemented."""
        del job_id
        _not_implemented()

    @app.get(
        "/api/jobs/{job_id}/events",
        response_class=StreamingResponse,
        responses={
            200: {"content": {"text/event-stream": {}}},
            **stub_responses,
        },
        tags=["jobs"],
    )
    def job_events(job_id: str) -> StreamingResponse:
        """Stream job progress events when orchestration is implemented."""
        del job_id
        _not_implemented()

    @app.patch(
        "/api/jobs/{job_id}/clips/{clip_id}",
        response_model=Clip,
        responses=stub_responses,
        tags=["clips"],
    )
    def update_clip(job_id: str, clip_id: str, _: ClipUpdate) -> Clip:
        """Edit a clip selection when job editing is implemented."""
        del job_id, clip_id
        _not_implemented()

    @app.get(
        "/api/jobs/{job_id}/clips/{clip_id}/video",
        responses={200: {"content": {"video/mp4": {}}}, **stub_responses},
        tags=["clips"],
    )
    def clip_video(job_id: str, clip_id: str) -> Response:
        """Serve a rendered MP4 when rendering is implemented."""
        del job_id, clip_id
        _not_implemented()

    @app.get(
        "/api/jobs/{job_id}/clips/{clip_id}/thumbnail",
        responses={200: {"content": {"image/jpeg": {}}}, **stub_responses},
        tags=["clips"],
    )
    def clip_thumbnail(job_id: str, clip_id: str) -> Response:
        """Serve a JPEG thumbnail when rendering is implemented."""
        del job_id, clip_id
        _not_implemented()

    @app.get(
        "/api/jobs/{job_id}/clips/{clip_id}/preview",
        responses={200: {"content": {"video/mp4": {}}}, **stub_responses},
        tags=["clips"],
    )
    def clip_preview(job_id: str, clip_id: str) -> Response:
        """Serve a captionless preview proxy when rendering is implemented."""
        del job_id, clip_id
        _not_implemented()

    @app.get(
        "/api/jobs/{job_id}/clips/{clip_id}/captions.ass",
        responses={200: {"content": {"text/plain": {}}}, **stub_responses},
        tags=["clips"],
    )
    def clip_captions(job_id: str, clip_id: str) -> Response:
        """Serve generated ASS captions when captions are implemented."""
        del job_id, clip_id
        _not_implemented()

    @app.get(
        "/api/jobs/{job_id}/download.zip",
        responses={200: {"content": {"application/zip": {}}}, **stub_responses},
        tags=["jobs"],
    )
    def download_job(job_id: str) -> Response:
        """Serve a job archive when export is implemented."""
        del job_id
        _not_implemented()

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
