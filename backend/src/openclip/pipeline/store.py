"""SQLite-backed job and upload persistence (SQLModel)."""

import json
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine

from openclip.core.config import Settings
from openclip.core.models import Job, JobStatus


class JobRow(SQLModel, table=True):
    """One persisted job: the full Job contract as JSON plus status columns."""

    id: str = Field(primary_key=True)
    status: str = Field(index=True)
    payload: str
    created_at: str
    updated_at: str


class UploadRow(SQLModel, table=True):
    """A streamed upload: stored path plus the original filename."""

    id: str = Field(primary_key=True)
    filename: str
    path: str
    size_bytes: int
    created_at: str


_engine: object | None = None
_engine_lock = threading.Lock()


def _get_engine(settings: Settings):
    """Lazily create the SQLite engine inside the data dir."""
    global _engine
    with _engine_lock:
        if _engine is None:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(
                f"sqlite:///{settings.data_dir / 'openclip.db'}",
                connect_args={"check_same_thread": False},
            )
            SQLModel.metadata.create_all(_engine)  # type: ignore[attr-defined]
        return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests only)."""
    global _engine
    with _engine_lock:
        _engine = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def job_dir(settings: Settings, job_id: str) -> Path:
    """Return (and create) the per-job work directory."""
    path = settings.data_dir / "jobs" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_job(settings: Settings, job: Job) -> None:
    """Insert or update a job row."""
    engine = _get_engine(settings)
    row = JobRow(
        id=job.id,
        status=str(job.status),
        payload=job.model_dump_json(),
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )
    with Session(engine) as session:  # type: ignore[arg-type]
        existing = session.get(JobRow, job.id)
        if existing is None:
            session.add(row)
        else:
            existing.status = row.status
            existing.payload = row.payload
            existing.updated_at = row.updated_at
            session.add(existing)
        session.commit()


def load_job(settings: Settings, job_id: str) -> Job | None:
    """Load a job by id, or ``None`` when absent."""
    engine = _get_engine(settings)
    with Session(engine) as session:  # type: ignore[arg-type]
        row = session.get(JobRow, job_id)
        if row is None:
            return None
        return Job.model_validate_json(row.payload)


def list_jobs(settings: Settings) -> list[Job]:
    """List all jobs, newest first."""
    engine = _get_engine(settings)
    from sqlmodel import select

    with Session(engine) as session:  # type: ignore[arg-type]
        rows = session.exec(select(JobRow).order_by(JobRow.created_at.desc())).all()  # type: ignore[attr-defined]
        return [Job.model_validate_json(row.payload) for row in rows]


def delete_job_row(settings: Settings, job_id: str) -> bool:
    """Delete the job row; returns True when a row existed."""
    engine = _get_engine(settings)
    with Session(engine) as session:  # type: ignore[arg-type]
        row = session.get(JobRow, job_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def mark_interrupted(settings: Settings) -> int:
    """Mark jobs left ``running``/``queued`` by a previous process as failed."""
    from openclip.core.models import JobError

    count = 0
    for job in list_jobs(settings):
        if job.status in (JobStatus.RUNNING, JobStatus.QUEUED):
            job.status = JobStatus.FAILED
            job.error = JobError(
                code="interrupted", message="The server restarted during this job."
            )
            save_job(settings, job)
            count += 1
    return count


def new_job_id() -> str:
    """Return a fresh uuid4-hex job id."""
    return uuid.uuid4().hex


def new_upload_id() -> str:
    """Return a fresh uuid4-hex upload id."""
    return uuid.uuid4().hex


def save_upload(settings: Settings, upload_id: str, filename: str, path: Path, size: int) -> None:
    """Persist an upload row."""
    engine = _get_engine(settings)
    row = UploadRow(
        id=upload_id,
        filename=filename,
        path=str(path),
        size_bytes=size,
        created_at=_now(),
    )
    with Session(engine) as session:  # type: ignore[arg-type]
        session.add(row)
        session.commit()


def get_upload(settings: Settings, upload_id: str) -> UploadRow | None:
    """Load an upload row by id."""
    engine = _get_engine(settings)
    with Session(engine) as session:  # type: ignore[arg-type]
        return session.get(UploadRow, upload_id)


def upload_dir(settings: Settings, upload_id: str) -> Path:
    """Return the directory holding one upload."""
    return settings.data_dir / "uploads" / upload_id


def delete_upload(settings: Settings, upload_id: str) -> None:
    """Remove an upload row and its files."""
    engine = _get_engine(settings)
    with Session(engine) as session:  # type: ignore[arg-type]
        row = session.get(UploadRow, upload_id)
        if row is not None:
            session.delete(row)
            session.commit()
    shutil.rmtree(upload_dir(settings, upload_id), ignore_errors=True)


def delete_job_files(settings: Settings, job_id: str) -> None:
    """Remove the whole job directory."""
    shutil.rmtree(settings.data_dir / "jobs" / job_id, ignore_errors=True)


def touch_job(settings: Settings, job: Job) -> None:
    """Persist a job mutation helper (alias kept for readability)."""
    save_job(settings, job)


def utcnow() -> datetime:
    """Timezone-aware current time for Job timestamps."""
    return datetime.now(UTC)


def dumps_job(job: Job) -> str:
    """Serialize a Job for storage."""
    return json.dumps(job.model_dump(mode="json"))
