"""Edge-case matrix: HTTP API, uploads, SSE, PATCH, cancellation and recovery.

Heavy stages are faked (no Whisper, no LLM, no real render) so the whole file
runs in seconds; the two cases that need real media use tiny lavfi fixtures.
"""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openclip.core.config import Settings
from openclip.core.errors import OpenClipError
from openclip.core.models import (
    Clip,
    ClipSource,
    Job,
    JobOptions,
    JobSource,
    JobStatus,
    MediaInfo,
    Transcript,
)
from openclip.ingest.errors import IngestFailure
from openclip.pipeline import store
from openclip.pipeline.orchestrator import Pipeline
from openclip.pipeline.queue import EventBus, JobQueue

# ---------- shared fixtures ----------


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    return settings


def _media(path: str = "/tmp/fake.mp4", duration: float = 10.0) -> MediaInfo:
    return MediaInfo(path=path, duration=duration, width=1280, height=720, fps=30.0, has_audio=True)


def _transcript() -> Transcript:
    return Transcript(language="en", duration=10.0, model="fake", segments=[])


def _clips() -> list[Clip]:
    return [
        Clip(
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
    ]


def _job(settings: Settings, status: JobStatus = JobStatus.QUEUED) -> Job:
    return Job(
        id=store.new_job_id(),
        status=status,
        progress=0.0,
        source=JobSource(type="upload", value="/tmp/fake.mp4"),
        options=JobOptions(min_clip_seconds=1.0, max_clip_seconds=60.0),
        created_at=store.utcnow(),
        updated_at=store.utcnow(),
    )


def _patch_pipeline(monkeypatch, ingest_fn=None) -> None:
    import openclip.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "run_ingest", ingest_fn or (lambda *a, **k: _media()))
    monkeypatch.setattr(orch, "run_transcribe", lambda *a, **k: _transcript())
    monkeypatch.setattr(orch, "run_analyze", lambda *a, **k: _clips())

    def fake_render(
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
        (clip_dir / "clip.mp4").write_bytes(b"fake mp4")
        (clip_dir / "preview.mp4").write_bytes(b"fake preview")
        (clip_dir / "thumbnail.jpg").write_bytes(b"fake jpg")
        (clip_dir / "captions.ass").write_text("fake ass")
        clip.output_file = str(clip_dir / "clip.mp4")
        clip.thumbnail = str(clip_dir / "thumbnail.jpg")

    monkeypatch.setattr(orch, "_render_one_clip", fake_render)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from openclip.api.app import create_app

    store.reset_engine()
    _patch_pipeline(monkeypatch)
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    return TestClient(create_app(settings, pipeline=pipe)), pipe, settings


# ---------- malformed input ----------


def test_malformed_json_body_uses_the_shared_error_shape(client) -> None:
    c, _, _ = client
    resp = c.post("/api/jobs", content=b"{not json", headers={"content-type": "application/json"})
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == "invalid_request"
    assert "detail" not in body


def test_unknown_fields_and_bad_types_are_rejected_cleanly(client) -> None:
    c, _, settings = client
    up = store.upload_dir(settings, "up1")
    up.mkdir(parents=True)
    (up / "v.mp4").write_bytes(b"x")
    store.save_upload(settings, "up1", "v.mp4", up / "v.mp4", 1)
    bad_type = c.post(
        "/api/jobs",
        json={"source": {"type": "upload", "value": "up1"}, "options": {"max_clips": "lots"}},
    )
    assert bad_type.status_code == 422 and bad_type.json()["code"] == "invalid_request"
    unknown = c.post(
        "/api/jobs",
        json={"source": {"type": "upload", "value": "up1"}, "options": {"nope": 1}},
    )
    assert unknown.status_code in (201, 422)  # extra options are ignored by JobOptions


def test_huge_json_body_is_rejected_without_side_effects(client) -> None:
    c, _, settings = client
    payload = json.dumps({"source": {"type": "upload", "value": "x" * 200}, "options": {}})
    # 8 MB of padding: the API must not try to persist it as a job.
    resp = c.post(
        "/api/jobs",
        content=payload[:-1] + ', "padding": "' + "a" * 8_000_000 + '"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code in (400, 413, 422)
    assert store.list_jobs(settings) == []


def test_bad_ids_and_traversal_on_every_route(client) -> None:
    c, _, settings = client
    job = _job(settings, JobStatus.COMPLETED)
    job.clips = _clips()
    store.save_job(settings, job)
    bad_jobs = ["../../etc/passwd", "notanid", "0" * 31, "0" * 33, "%2e%2e%2f"]
    for bad in bad_jobs:
        assert c.get(f"/api/jobs/{bad}").status_code == 404
        assert c.get(f"/api/jobs/{bad}/events").status_code == 404
        assert c.get(f"/api/jobs/{bad}/download.zip").status_code == 404
        assert c.delete(f"/api/jobs/{bad}").status_code == 404
    good = f"/api/jobs/{job.id}/clips"
    for bad in ["..%2F..%2Fetc", "clip-..", "%2e%2e", "x" * 300]:
        assert c.get(f"{good}/{bad}/video").status_code in (404, 400, 422)
        assert c.get(f"{good}/{bad}/thumbnail").status_code in (404, 400, 422)
        assert c.get(f"{good}/{bad}/preview").status_code in (404, 400, 422)
        assert c.get(f"{good}/{bad}/captions.ass").status_code in (404, 400, 422)


# ---------- uploads ----------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("..", "upload.bin"),
        (".", "upload.bin"),
        ("clip with spaces.mp4", "clip_with_spaces.mp4"),
        ("café-thé.mp4", "caf_-th_.mp4"),
        ("quote'apostrophe.mp4", "quote_apostrophe.mp4"),
        ("[bracket].mp4", "_bracket_.mp4"),
        ("-leading-dash.mp4", "-leading-dash.mp4"),
    ],
)
def test_upload_names_are_sanitised(client, name: str, expected: str) -> None:
    c, _, settings = client
    resp = c.post("/api/uploads", files={"file": (name, b"x" * 32, "video/mp4")})
    assert resp.status_code == 201
    body = resp.json()
    assert body["filename"] == expected
    assert body["size_bytes"] == 32
    row = store.get_upload(settings, body["upload_id"])
    assert row is not None and Path(row.path).is_file()


def test_very_long_upload_name_is_capped(client) -> None:
    c, _, settings = client
    resp = c.post("/api/uploads", files={"file": ("a" * 400 + ".mp4", b"x" * 8, "video/mp4")})
    assert resp.status_code == 201
    stored = resp.json()["filename"]
    assert len(stored) <= 180
    assert stored.endswith(".mp4")
    row = store.get_upload(settings, resp.json()["upload_id"])
    assert row is not None and Path(row.path).is_file()


def test_upload_over_the_limit_leaves_nothing_behind(client) -> None:
    c, _, settings = client
    settings.max_upload_mb = 1
    resp = c.post(
        "/api/uploads", files={"file": ("big.mp4", b"x" * (2 * 1024 * 1024), "video/mp4")}
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "too_large"
    uploads = settings.data_dir / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())


def test_upload_disk_full_answers_507_and_cleans_up(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno as errno_mod

    c, _, settings = client
    real_write = Path.write_bytes

    def failing_write(self: Path, data: bytes) -> int:
        if self.name.endswith(".mp4"):
            raise OSError(errno_mod.ENOSPC, "No space left on device")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    # The handler opens a file object; patch that instead so ENOSPC is raised mid-write.
    import builtins

    real_open = builtins.open

    class _FullFile:
        def __init__(self, handle):
            self._handle = handle

        def write(self, data):  # noqa: ANN001
            raise OSError(errno_mod.ENOSPC, "No space left on device")

        def close(self) -> None:
            self._handle.close()

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            self.close()

    def fake_open(path, mode="r", *args, **kwargs):  # noqa: ANN001
        handle = real_open(path, mode, *args, **kwargs)
        if "w" in mode:
            return _FullFile(handle)
        return handle

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(
        Path, "open", lambda self, mode="r", *a, **k: fake_open(self, mode, *a, **k)
    )
    resp = c.post("/api/uploads", files={"file": ("v.mp4", b"x" * 16, "video/mp4")})
    assert resp.status_code == 507
    body = resp.json()
    assert body["code"] == "disk_full"
    uploads = settings.data_dir / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())


def test_upload_then_job_runs_to_completion(client) -> None:
    c, _, settings = client
    up = store.upload_dir(settings, "up9")
    up.mkdir(parents=True)
    (up / "v.mp4").write_bytes(b"x")
    store.save_upload(settings, "up9", "v.mp4", up / "v.mp4", 1)
    resp = c.post("/api/jobs", json={"source": {"type": "upload", "value": "up9"}, "options": {}})
    assert resp.status_code == 201
    job_id = resp.json()["id"]
    deadline = time.time() + 15
    status = "queued"
    while time.time() < deadline:
        status = c.get(f"/api/jobs/{job_id}").json()["status"]
        if status in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert status == "completed"


# ---------- job creation ----------


@pytest.mark.parametrize(
    "source",
    [
        {"type": "url", "value": "http://127.0.0.1:8080/x.mp4"},
        {"type": "url", "value": "http://localhost/x.mp4"},
        {"type": "url", "value": "http://169.254.169.254/latest/"},
        {"type": "url", "value": "http://[::1]/x.mp4"},
        {"type": "upload", "value": "does-not-exist"},
    ],
)
def test_bad_sources_are_rejected(client, source: dict) -> None:
    c, _, _ = client
    resp = c.post("/api/jobs", json={"source": source, "options": {}})
    assert resp.status_code == 400, source
    assert resp.json()["code"], source


def test_non_http_url_is_rejected_by_the_contract(client) -> None:
    """`file://` never reaches the URL checker: the model itself forbids it."""
    c, _, _ = client
    resp = c.post("/api/jobs", json={"source": {"type": "url", "value": "file:///etc/passwd"}})
    assert resp.status_code == 422 and resp.json()["code"] == "invalid_request"


def test_two_jobs_run_fifo_and_one_at_a_time(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()
    order: list[str] = []
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow_ingest(source, *a, **k):  # noqa: ANN001, ARG001
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            order.append(str(getattr(source, "value", source)))
        time.sleep(0.15)
        with lock:
            active["now"] -= 1
        return _media()

    _patch_pipeline(monkeypatch, slow_ingest)
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    jobs = []
    for index in range(2):
        job = _job(settings)
        job.source = JobSource(type="upload", value=f"job{index}.mp4")
        pipe.submit(job)
        jobs.append(job)
    deadline = time.time() + 20
    while time.time() < deadline:
        states = [pipe.job(j.id).status for j in jobs]  # type: ignore[union-attr]
        if all(s == JobStatus.COMPLETED for s in states):
            break
        time.sleep(0.05)
    assert order == ["job0.mp4", "job1.mp4"], "FIFO order"
    assert active["max"] == 1, "never two jobs at once"
    assert [pipe.job(j.id).status for j in jobs] == [JobStatus.COMPLETED] * 2  # type: ignore[union-attr]


# ---------- cancellation ----------


def test_cancelled_job_reports_cancelled_not_failed(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()

    def cancelling_ingest(source, *a, cancel=None, **k):  # noqa: ANN001, ARG001
        for _ in range(200):
            if cancel is not None and cancel.is_set():
                raise IngestFailure("cancelled", "Operation cancelled.")
            time.sleep(0.01)
        return _media()

    _patch_pipeline(monkeypatch, cancelling_ingest)
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    pipe.submit(job)
    time.sleep(0.2)
    assert pipe.cancel(job.id) is True
    deadline = time.time() + 10
    status = None
    while time.time() < deadline:
        current = pipe.job(job.id)
        status = current.status if current else None
        if status in (JobStatus.CANCELLED, JobStatus.FAILED, JobStatus.COMPLETED):
            break
        time.sleep(0.05)
    assert status == JobStatus.CANCELLED
    assert pipe.job(job.id).error is None  # type: ignore[union-attr]


def test_cancel_unknown_job_returns_false(tmp_path: Path) -> None:
    store.reset_engine()
    pipe = Pipeline(_settings(tmp_path))
    assert pipe.cancel("0" * 32) is False


def test_delete_during_run_does_not_resurrect_the_job(tmp_path: Path, monkeypatch) -> None:
    store.reset_engine()

    def failing_ingest(source, *a, cancel=None, **k):  # noqa: ANN001, ARG001
        for _ in range(100):
            if cancel is not None and cancel.is_set():
                raise IngestFailure("cancelled", "Operation cancelled.")
            time.sleep(0.01)
        raise IngestFailure("unsupported_file", "boom")

    _patch_pipeline(monkeypatch, failing_ingest)
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    pipe.submit(job)
    time.sleep(0.15)
    assert pipe.delete(job.id) is True
    time.sleep(1.0)
    assert pipe.job(job.id) is None, "a deleted job must stay deleted"
    # Assert on the path directly: store.job_dir() would recreate the directory.
    assert not (settings.data_dir / "jobs" / job.id).exists()


def test_delete_running_job_via_api_returns_204_and_leaves_no_row(client) -> None:
    c, pipe, settings = client
    job = _job(settings, JobStatus.RUNNING)
    store.save_job(settings, job)
    assert c.delete(f"/api/jobs/{job.id}").status_code == 204
    assert c.get(f"/api/jobs/{job.id}").status_code == 404


# ---------- restart / resume ----------


def test_restart_marks_running_jobs_interrupted_and_resumable(tmp_path: Path) -> None:
    store.reset_engine()
    settings = _settings(tmp_path)
    job = _job(settings, JobStatus.RUNNING)
    store.save_job(settings, job)
    store.reset_engine()  # simulate a fresh process
    assert store.mark_interrupted(settings) == 1
    loaded = store.load_job(settings, job.id)
    assert loaded is not None
    assert loaded.status == JobStatus.FAILED
    assert loaded.error is not None and loaded.error.code == "interrupted"


def test_resume_after_mid_render_interruption_has_no_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    """Existing artifacts are reused: no duplicate outputs, no re-encode."""
    store.reset_engine()
    renders: list[str] = []
    _patch_pipeline(monkeypatch)
    import openclip.pipeline.orchestrator as orch

    real_render = orch._render_one_clip

    def counting_render(*a, **k):  # noqa: ANN002, ANN003
        clip = a[1]
        renders.append(clip.id)
        return real_render(*a, **k)

    monkeypatch.setattr(orch, "_render_one_clip", counting_render)
    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    first = pipe.execute(job)
    assert first.status == JobStatus.COMPLETED
    clip_dir = store.job_dir(settings, job.id) / "clips" / "clip-001"
    before = {p.name: p.stat().st_size for p in clip_dir.iterdir()}

    resumed = Job.model_validate(first.model_dump())
    resumed.status = JobStatus.RUNNING
    final = pipe.execute(resumed)
    assert final.status == JobStatus.COMPLETED
    after = {p.name: p.stat().st_size for p in clip_dir.iterdir()}
    assert before == after, "resume must not duplicate or rewrite artifacts"
    assert sorted(p.name for p in clip_dir.iterdir()) == [
        "captions.ass",
        "clip.mp4",
        "preview.mp4",
        "thumbnail.jpg",
    ]


# ---------- PATCH ----------


@pytest.fixture
def seeded_client(client):
    c, pipe, settings = client
    job = _job(settings, JobStatus.COMPLETED)
    job.clips = _clips()
    store.save_job(settings, job)
    workdir = store.job_dir(settings, job.id)
    (workdir / "media.json").write_text(_media().model_dump_json())
    (workdir / "transcript.json").write_text(
        json.dumps({"transcript": _transcript().model_dump(mode="json")})
    )
    clip_dir = workdir / "clips" / job.clips[0].id
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip.mp4").write_bytes(b"old clip")
    (clip_dir / "preview.mp4").write_bytes(b"old preview")
    (clip_dir / "thumbnail.jpg").write_bytes(b"old thumb")
    (clip_dir / "captions.ass").write_text("old ass")
    job.clips[0].output_file = str(clip_dir / "clip.mp4")
    job.clips[0].thumbnail = str(clip_dir / "thumbnail.jpg")
    store.save_job(settings, job)
    # Re-render writes new bytes so tests can see the swap.
    import openclip.pipeline.orchestrator as orch

    def fake_artifacts(job_, clip_, media, transcript, crop, style, clip_dir_, settings_):
        clip_dir_.mkdir(parents=True, exist_ok=True)
        (clip_dir_ / "clip.mp4").write_bytes(b"new clip " + style.id.encode())
        (clip_dir_ / "preview.mp4").write_bytes(b"new preview")
        (clip_dir_ / "thumbnail.jpg").write_bytes(b"new thumb")
        (clip_dir_ / "captions.ass").write_text(f"; {style.id} {clip_.start}-{clip_.end}")

    monkeypatch_target = orch
    monkeypatch_target._render_clip_artifacts = fake_artifacts  # type: ignore[assignment]
    yield c, pipe, settings, job
    monkeypatch_target._render_clip_artifacts = _ORIGINAL_ARTIFACTS  # type: ignore[assignment]


import openclip.pipeline.orchestrator as _orch_mod  # noqa: E402

_ORIGINAL_ARTIFACTS = _orch_mod._render_clip_artifacts


def _wait_for_file(path: Path, needle: bytes, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file() and needle in path.read_bytes():
            return True
        time.sleep(0.02)
    return False


def test_patch_style_rerender_swaps_artifacts(seeded_client) -> None:
    c, _, settings, job = seeded_client
    clip_id = job.clips[0].id
    resp = c.patch(
        f"/api/jobs/{job.id}/clips/{clip_id}",
        json={"caption_style_id": "minimal-clean"},
    )
    assert resp.status_code == 200
    clip_dir = store.job_dir(settings, job.id) / "clips" / clip_id
    assert _wait_for_file(clip_dir / "clip.mp4", b"new clip")
    assert "minimal-clean" in (clip_dir / "captions.ass").read_text()


def test_patch_same_bounds_is_accepted(seeded_client) -> None:
    c, _, _, job = seeded_client
    clip = job.clips[0]
    resp = c.patch(
        f"/api/jobs/{job.id}/clips/{clip.id}",
        json={"start": clip.start, "end": clip.end},
    )
    assert resp.status_code == 200
    assert resp.json()["start"] == clip.start and resp.json()["end"] == clip.end


def test_patch_min_and_max_length_bounds(seeded_client) -> None:
    c, _, _, job = seeded_client
    clip_id = job.clips[0].id
    too_short = c.patch(f"/api/jobs/{job.id}/clips/{clip_id}", json={"start": 0.0, "end": 0.5})
    assert too_short.status_code == 422 and too_short.json()["code"] == "invalid_duration"
    too_long = c.patch(f"/api/jobs/{job.id}/clips/{clip_id}", json={"start": 0.0, "end": 90.0})
    assert too_long.status_code == 422 and too_long.json()["code"] in (
        "invalid_duration",
        "out_of_range",
    )


def test_patch_rejects_unknown_style_and_bad_ranges(seeded_client) -> None:
    c, _, _, job = seeded_client
    clip_id = job.clips[0].id
    base = f"/api/jobs/{job.id}/clips/{clip_id}"
    assert c.patch(base, json={"caption_style_id": "nope"}).json()["code"] == "unknown_style"
    assert c.patch(base, json={"start": 4.0, "end": 2.0}).status_code == 422
    assert c.patch(base, json={"start": -1.0}).status_code == 422
    # Duration is validated before the source range, so 999 s reports the length.
    assert c.patch(base, json={"start": 0.0, "end": 999.0}).json()["code"] == "invalid_duration"


def test_patch_unknown_job_or_clip_is_404(seeded_client) -> None:
    c, _, settings, job = seeded_client
    assert c.patch(f"/api/jobs/{'0' * 32}/clips/{job.clips[0].id}", json={}).status_code == 404
    assert c.patch(f"/api/jobs/{job.id}/clips/nope", json={}).status_code == 404


def test_patch_concurrent_requests_conflict(seeded_client) -> None:
    c, pipe, _, job = seeded_client
    clip_id = job.clips[0].id
    lock = pipe.clip_rerender_lock(job.id, clip_id)
    assert lock.acquire(blocking=False)
    try:
        resp = c.patch(
            f"/api/jobs/{job.id}/clips/{clip_id}", json={"caption_style_id": "clean-pop"}
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "rerender_in_progress"
    finally:
        lock.release()


def test_patch_failure_keeps_previous_artifacts(seeded_client, monkeypatch) -> None:
    c, _, settings, job = seeded_client
    import openclip.pipeline.orchestrator as orch

    def boom(*_a, **_k):
        raise OpenClipError("simulated render failure")

    monkeypatch.setattr(orch, "_render_clip_artifacts", boom)
    clip_id = job.clips[0].id
    clip_dir = store.job_dir(settings, job.id) / "clips" / clip_id
    before = (clip_dir / "clip.mp4").read_bytes()
    resp = c.patch(
        f"/api/jobs/{job.id}/clips/{clip_id}", json={"caption_style_id": "minimal-clean"}
    )
    assert resp.status_code == 200
    time.sleep(0.4)
    assert (clip_dir / "clip.mp4").read_bytes() == before
    assert not [p for p in clip_dir.iterdir() if p.name.startswith(".rerender")]


# ---------- files and ranges ----------


def test_range_requests_cover_the_usual_shapes(seeded_client) -> None:
    c, _, _, job = seeded_client
    url = f"/api/jobs/{job.id}/clips/{job.clips[0].id}/video"
    full = c.get(url)
    assert full.status_code == 200
    size = len(full.content)
    first = c.get(url, headers={"Range": "bytes=0-0"})
    assert first.status_code == 206 and len(first.content) == 1
    assert first.headers["content-range"] == f"bytes 0-0/{size}"
    open_ended = c.get(url, headers={"Range": f"bytes={size - 2}-"})
    assert open_ended.status_code == 206 and len(open_ended.content) == 2
    past_end = c.get(url, headers={"Range": f"bytes={size + 10}-{size + 20}"})
    assert past_end.status_code == 416, "an unsatisfiable range must be 416 per RFC 7233"
    garbage = c.get(url, headers={"Range": "bytes=abc"})
    assert garbage.status_code == 400, "a malformed Range header is a client error"


def test_conditional_get_returns_304(seeded_client) -> None:
    c, _, _, job = seeded_client
    url = f"/api/jobs/{job.id}/clips/{job.clips[0].id}/video"
    etag = c.get(url).headers["etag"]
    assert c.get(url, headers={"If-None-Match": etag}).status_code == 304


def test_zip_requires_a_completed_job(client) -> None:
    c, _, settings = client
    job = _job(settings, JobStatus.RUNNING)
    store.save_job(settings, job)
    resp = c.get(f"/api/jobs/{job.id}/download.zip")
    assert resp.status_code == 409 and resp.json()["code"] == "not_ready"


def test_zip_skips_missing_artifacts(seeded_client) -> None:
    c, _, settings, job = seeded_client
    clip_dir = store.job_dir(settings, job.id) / "clips" / job.clips[0].id
    (clip_dir / "preview.mp4").unlink()
    resp = c.get(f"/api/jobs/{job.id}/download.zip")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"


# ---------- SSE ----------


def test_sse_frames_carry_ids_and_replay_from_last_event_id(client) -> None:
    c, pipe, settings = client
    job = _job(settings, JobStatus.RUNNING)
    store.save_job(settings, job)
    for i in range(4):
        pipe.bus.publish(
            job.id,
            "progress",
            {
                "job_id": job.id,
                "status": "running",
                "stage": "transcribe",
                "progress": i / 10,
                "message": f"step {i}",
            },
        )
    with c.stream("GET", f"/api/jobs/{job.id}/events") as resp:
        assert resp.status_code == 200
        chunk = next(resp.iter_text())
        assert chunk.startswith("event: progress")  # live snapshot first
    with c.stream("GET", f"/api/jobs/{job.id}/events", headers={"Last-Event-ID": "2"}) as resp:
        chunk = next(resp.iter_text())
        assert chunk.startswith("id: 3\n")  # resumes after the seen frame
        assert "step 2" in chunk


def test_sse_ignores_a_garbage_last_event_id(client) -> None:
    c, _, settings = client
    job = _job(settings, JobStatus.RUNNING)
    store.save_job(settings, job)
    with c.stream(
        "GET", f"/api/jobs/{job.id}/events", headers={"Last-Event-ID": "not-a-number"}
    ) as resp:
        assert resp.status_code == 200
        assert "event: progress" in next(resp.iter_text())


def test_sse_done_terminates_the_stream(client) -> None:
    """A finished job must not leave an SSE client hanging."""
    c, _, settings = client
    job = _job(settings, JobStatus.COMPLETED)
    store.save_job(settings, job)
    with c.stream("GET", f"/api/jobs/{job.id}/events") as resp:
        frames = list(resp.iter_text())
    joined = "".join(frames)
    assert "event: progress" in joined
    assert "event: done" in joined and "completed" in joined


def test_sse_failed_job_emits_a_terminal_error(client) -> None:
    from openclip.core.models import JobError

    c, _, settings = client
    job = _job(settings, JobStatus.FAILED)
    job.error = JobError(code="no_speech", message="No speech was detected.")
    store.save_job(settings, job)
    with c.stream("GET", f"/api/jobs/{job.id}/events") as resp:
        joined = "".join(resp.iter_text())
    assert "event: error" in joined and "no_speech" in joined


def test_sse_clip_scoped_error_keeps_the_stream_open(client) -> None:
    """A clip-scoped error must not close the stream; a later done must."""
    c, pipe, settings = client
    job = _job(settings, JobStatus.RUNNING)
    store.save_job(settings, job)

    def publish_soon() -> None:
        time.sleep(0.2)
        pipe.bus.publish(
            job.id,
            "error",
            {"job_id": job.id, "clip_id": "clip-001", "code": "render_failed", "message": "x"},
        )
        pipe.bus.publish(job.id, "done", {"job_id": job.id, "status": "completed", "progress": 1.0})

    threading.Thread(target=publish_soon, daemon=True).start()
    with c.stream("GET", f"/api/jobs/{job.id}/events") as resp:
        chunks: list[str] = []
        for chunk in resp.iter_text():
            chunks.append(chunk)
            if "event: done" in chunk:
                break
    joined = "".join(chunks)
    assert "clip-001" in joined, "the clip-scoped error must be delivered"
    assert "event: done" in joined, "the stream must end on done, not on the clip error"


def test_clip_scoped_error_frame_is_not_terminal() -> None:
    from openclip.api.app import _is_terminal_frame

    clip_error = (
        'id: 2\nevent: error\ndata: {"job_id":"j","clip_id":"c1",'
        '"code":"render_failed","message":"m"}\n\n'
    )
    job_error = 'id: 3\nevent: error\ndata: {"job_id":"j","code":"render_failed","message":"m"}\n\n'
    assert _is_terminal_frame(clip_error) is False
    assert _is_terminal_frame(job_error) is True


def test_event_bus_replays_only_newer_frames() -> None:
    bus = EventBus(replay=4)
    for i in range(6):
        bus.publish("j", "progress", {"i": i})
    q = bus.subscribe("j", after=10)
    assert q.empty()
    q = bus.subscribe("j", after=0)
    assert q.empty(), "after=0 means a live subscription, not a full replay"
    q = bus.subscribe("j", after=4)
    ids = []
    while not q.empty():
        frame = q.get_nowait()
        ids.append(int(frame.split("\n", 1)[0].split(":")[1]))
    assert ids == [5, 6]


def test_job_queue_runs_one_job_at_a_time() -> None:
    seen: list[str] = []
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def runner(job_id: str) -> None:
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
            seen.append(job_id)
        time.sleep(0.05)
        with lock:
            active["now"] -= 1

    queue_ = JobQueue(runner)
    for i in range(3):
        queue_.submit(f"job{i}")
    deadline = time.time() + 5
    while time.time() < deadline and len(seen) < 3:
        time.sleep(0.02)
    assert seen == ["job0", "job1", "job2"]
    assert active["peak"] == 1
