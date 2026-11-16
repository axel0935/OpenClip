"""Tests for the pipeline orchestrator, job store, queue and API."""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openclip.core.config import Settings
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
from openclip.pipeline import store
from openclip.pipeline.orchestrator import STAGE_WEIGHTS, Pipeline
from openclip.pipeline.queue import EventBus, JobQueue


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    return settings


def _job(settings: Settings, source: str = "upload") -> Job:
    return Job(
        id=store.new_job_id(),
        status=JobStatus.QUEUED,
        progress=0.0,
        source=JobSource(type="upload", value="/tmp/fake.mp4"),
        options=JobOptions(),
        created_at=store.utcnow(),
        updated_at=store.utcnow(),
    )


def _fake_media() -> MediaInfo:
    return MediaInfo(
        path="/tmp/fake.mp4", duration=10.0, width=1280, height=720, fps=30.0, has_audio=True
    )


def _fake_transcript() -> Transcript:
    return Transcript(language="en", duration=10.0, model="fake", segments=[])


def _fake_clips() -> list[Clip]:
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
        ),
    ]


@pytest.fixture
def patched_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A Pipeline whose heavy stages are faked; render writes tiny files."""
    import openclip.pipeline.orchestrator as orch

    settings = _settings(tmp_path)
    monkeypatch.setattr(orch, "run_ingest", lambda *a, **k: _fake_media())
    monkeypatch.setattr(orch, "run_transcribe", lambda *a, **k: _fake_transcript())
    monkeypatch.setattr(orch, "run_analyze", lambda *a, **k: _fake_clips())

    def fake_render(
        job,
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
        clip_dir = workdir / "clips" / clip.id
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"fake mp4")
        (clip_dir / "preview.mp4").write_bytes(b"fake preview")
        (clip_dir / "thumbnail.jpg").write_bytes(b"fake jpg")
        (clip_dir / "captions.ass").write_text("fake ass")
        clip.output_file = str(clip_dir / "clip.mp4")
        clip.thumbnail = str(clip_dir / "thumbnail.jpg")

    monkeypatch.setattr(orch, "_render_one_clip", fake_render)
    pipe = Pipeline(settings)
    return pipe, settings


def test_stage_weights_sum_to_one() -> None:
    assert sum(STAGE_WEIGHTS.values()) == pytest.approx(1.0)


def test_pipeline_runs_to_completion(patched_pipeline) -> None:
    pipe, settings = patched_pipeline
    job = _job(settings)
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED
    assert final.clips and final.clips[0].output_file
    workdir = store.job_dir(settings, job.id)
    assert (workdir / "media.json").is_file()
    assert (workdir / "transcript.json").is_file()
    assert (workdir / "clips.json").is_file()
    assert (workdir / "results.json").is_file()


def test_pipeline_resume_skips_ingested(patched_pipeline, monkeypatch) -> None:
    pipe, settings = patched_pipeline
    import openclip.pipeline.orchestrator as orch

    calls = {"ingest": 0}
    real_fake = orch.run_ingest

    def counting(*a, **k):
        calls["ingest"] += 1
        return real_fake(*a, **k)

    job = _job(settings)
    pipe.execute(job)
    # Second run on same workdir: ingest artifact exists -> skipped.
    job2 = _job(settings)
    job2.id = job.id
    monkeypatch.setattr(orch, "run_ingest", counting)
    pipe.execute(job2)
    assert calls["ingest"] == 0


def test_queue_fifo_and_cancel() -> None:
    order: list[str] = []
    gate = threading.Event()
    q: JobQueue

    def runner(job_id: str) -> None:
        handle = q.handle(job_id)
        if handle is not None and handle.cancel.is_set():
            return
        order.append(job_id)
        if job_id == "a":
            gate.wait(2)

    q = JobQueue(runner)
    try:
        q.submit("a")
        q.submit("b")
        deadline = time.time() + 5
        while time.time() < deadline and not order:
            time.sleep(0.05)
        assert order == ["a"]
        q.cancel("b")
        gate.set()
        deadline = time.time() + 5
        while time.time() < deadline and q.is_active("b"):
            time.sleep(0.05)
        # 'b' was cancelled before running -> never appended
        assert order == ["a"]
    finally:
        gate.set()
        q._handles.clear()


def test_event_bus_pubsub() -> None:
    bus = EventBus()
    q = bus.subscribe("j1")
    bus.publish("j1", "progress", {"progress": 0.5})
    frame = q.get(timeout=2)
    assert "event: progress" in frame and '"progress": 0.5' in frame
    bus.unsubscribe("j1", q)


def test_store_roundtrip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store.reset_engine()
    job = _job(settings)
    store.save_job(settings, job)
    loaded = store.load_job(settings, job.id)
    assert loaded is not None and loaded.id == job.id
    assert store.load_job(settings, "0" * 32) is None
    assert store.list_jobs(settings)[0].id == job.id
    store.delete_job_row(settings, job.id)
    assert store.load_job(settings, job.id) is None


def test_mark_interrupted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store.reset_engine()
    job = _job(settings)
    job.status = JobStatus.RUNNING
    store.save_job(settings, job)
    assert store.mark_interrupted(settings) == 1
    loaded = store.load_job(settings, job.id)
    assert loaded is not None and loaded.status == JobStatus.FAILED
    assert loaded.error is not None and loaded.error.code == "interrupted"


# --- API -------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import openclip.pipeline.orchestrator as orch
    from openclip.api.app import create_app

    settings = _settings(tmp_path)
    monkeypatch.setattr(orch, "run_ingest", lambda *a, **k: _fake_media())
    monkeypatch.setattr(orch, "run_transcribe", lambda *a, **k: _fake_transcript())
    monkeypatch.setattr(orch, "run_analyze", lambda *a, **k: _fake_clips())

    def fake_render(
        job,
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
        clip_dir = workdir / "clips" / clip.id
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"fake mp4")
        (clip_dir / "preview.mp4").write_bytes(b"fake preview")
        (clip_dir / "thumbnail.jpg").write_bytes(b"fake jpg")
        (clip_dir / "captions.ass").write_text("fake ass")
        clip.output_file = str(clip_dir / "clip.mp4")
        clip.thumbnail = str(clip_dir / "thumbnail.jpg")

    monkeypatch.setattr(orch, "_render_one_clip", fake_render)
    pipe = Pipeline(settings)
    app = create_app(settings)
    app.state.pipeline = pipe
    return TestClient(app), pipe, settings


def test_health(client) -> None:
    c, _, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


def test_caption_styles(client) -> None:
    c, _, _ = client
    resp = c.get("/api/caption-styles")
    assert resp.status_code == 200
    styles = resp.json()
    assert len(styles) >= 8 and all("id" in s for s in styles)


def test_create_job_url_ssrf_rejected(client) -> None:
    c, _, _ = client
    resp = c.post(
        "/api/jobs",
        json={
            "source": {"type": "url", "value": "http://127.0.0.1:8080/x.mp4"},
            "options": {},
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] and body["message"]


def test_create_job_and_poll(client) -> None:
    c, pipe, settings = client
    resp = c.post(
        "/api/jobs",
        json={
            "source": {"type": "upload", "value": "/tmp/nonexistent.mp4"},
            "options": {"max_clips": 2},
        },
    )
    # upload id unknown -> 400
    assert resp.status_code == 400
    # create a real upload row pointing at a fake file
    up_dir = store.upload_dir(settings, "up1")
    up_dir.mkdir(parents=True, exist_ok=True)
    fake = up_dir / "v.mp4"
    fake.write_bytes(b"x")
    store.save_upload(settings, "up1", "v.mp4", fake, 1)
    resp = c.post(
        "/api/jobs",
        json={
            "source": {"type": "upload", "value": "up1"},
            "options": {"max_clips": 2},
        },
    )
    assert resp.status_code == 201
    job = resp.json()
    assert job["status"] == "queued" and job["id"]
    # wait for completion
    got: dict = {}
    deadline = time.time() + 15
    while time.time() < deadline:
        got = c.get(f"/api/jobs/{job['id']}").json()
        if got["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert got["status"] == "completed", got.get("error")
    assert got["clips"]


def test_get_job_404_and_bad_id(client) -> None:
    c, _, _ = client
    assert c.get("/api/jobs/" + "0" * 32).status_code == 404
    assert c.get("/api/jobs/notanid").status_code == 404
    body = c.get("/api/jobs/notanid").json()
    assert body["code"] == "not_found"


def test_upload_and_size_limit(client, tmp_path: Path) -> None:
    c, _, settings = client
    settings.max_upload_mb = 1
    resp = c.post("/api/uploads", files={"file": ("clip one.mp4", b"x" * 100, "video/mp4")})
    assert resp.status_code == 201
    data = resp.json()
    assert data["upload_id"] and data["filename"] == "clip_one.mp4"
    # file exists under data dir
    row_path = store.get_upload(settings, data["upload_id"])
    assert row_path is not None and Path(row_path.path).is_file()


def test_patch_clip_rerenders(client) -> None:
    c, pipe, settings = client
    # seed a completed job
    job = _job(settings)
    job.status = JobStatus.COMPLETED
    clip = _fake_clips()[0]
    job.clips = [clip]
    store.save_job(settings, job)
    workdir = store.job_dir(settings, job.id)
    (workdir / "media.json").write_text(_fake_media().model_dump_json())
    (workdir / "transcript.json").write_text(
        json.dumps({"transcript": _fake_transcript().model_dump(mode="json")})
    )
    from openclip.core.models import CropPath

    (workdir / f"crop-{clip.id}.json").write_text(
        CropPath.model_validate(
            {
                "layout": "fit",
                "source_width": 1280,
                "source_height": 720,
                "keyframes": [{"t": 0, "x": 0, "y": 0, "w": 1280, "h": 720}],
            }
        ).model_dump_json()
    )
    # make render produce files so clip_dir exists

    def fake_render(
        job_,
        clip_,
        media,
        transcript,
        crop,
        style,
        workdir_,
        settings_,
        bus,
        cancel,
        *,
        progress_fn=None,
    ):
        clip_dir = workdir_ / "clips" / clip_.id
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"new")
        clip_.output_file = str(clip_dir / "clip.mp4")

    # rerender_clip uses module-level _render_one_clip -> patch via monkeypatch fixture? use direct
    resp = c.patch(
        f"/api/jobs/{job.id}/clips/{clip.id}",
        json={"start": 1.0, "end": 4.0, "caption_style_id": "minimal-clean"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["start"] == 1.0 and body["end"] == 4.0


def test_file_routes_path_traversal(client) -> None:
    c, pipe, settings = client
    job = _job(settings)
    job.status = JobStatus.COMPLETED
    clip = _fake_clips()[0]
    job.clips = [clip]
    store.save_job(settings, job)
    clip_dir = store.job_dir(settings, job.id) / "clips" / clip.id
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip.mp4").write_bytes(b"mp4")
    (clip_dir / "thumbnail.jpg").write_bytes(b"jpg")
    (clip_dir / "preview.mp4").write_bytes(b"prev")
    (clip_dir / "captions.ass").write_text("ass")
    clip.output_file = str(clip_dir / "clip.mp4")
    clip.thumbnail = str(clip_dir / "thumbnail.jpg")
    store.save_job(settings, job)
    base = f"/api/jobs/{job.id}/clips/{clip.id}"
    assert c.get(base + "/video").status_code == 200
    assert c.get(base + "/thumbnail").status_code == 200
    assert c.get(base + "/preview").status_code == 200
    assert c.get(base + "/captions.ass").status_code == 200
    # traversal attempt via clip_id
    resp = c.get(f"/api/jobs/{job.id}/clips/..%2F..%2Fetc/video")
    assert resp.status_code in (404, 400, 422)
    # range request
    resp = c.get(base + "/video", headers={"Range": "bytes=0-1"})
    assert resp.status_code == 206
    assert resp.headers["content-range"].startswith("bytes 0-1/")


def test_download_zip(client) -> None:
    c, pipe, settings = client
    job = _job(settings)
    job.status = JobStatus.COMPLETED
    clip = _fake_clips()[0]
    job.clips = [clip]
    store.save_job(settings, job)
    clip_dir = store.job_dir(settings, job.id) / "clips" / clip.id
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip.mp4").write_bytes(b"mp4")
    store.save_job(settings, job)
    resp = c.get(f"/api/jobs/{job.id}/download.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.content[:2] == b"PK"


def test_events_sse_snapshot(client) -> None:
    c, pipe, settings = client
    job = _job(settings)
    store.save_job(settings, job)
    with c.stream("GET", f"/api/jobs/{job.id}/events") as resp:
        assert resp.status_code == 200
        chunk = next(resp.iter_text())
        assert "event: progress" in chunk and job.id in chunk


def test_full_pipeline_real_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: fake ASR/analyze, real reframe-free render path -> real MP4."""
    pytest.importorskip("numpy")  # vision extra required by the reframe import
    import subprocess

    import openclip.pipeline.orchestrator as orch
    import openclip.reframe as reframe_mod
    from openclip.core.models import CropPath

    video = tmp_path / "src20.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=20:size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=20",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(video),
        ],
        check=True,
    )
    media = MediaInfo(
        path=str(video), duration=20.0, width=1280, height=720, fps=30.0, has_audio=True
    )
    clip = Clip(
        id="clip-001",
        start=2.0,
        end=7.0,
        score=90.0,
        title="A",
        hook="h",
        hashtags=[],
        rationale="r",
        source=ClipSource.HEURISTIC,
    )

    monkeypatch.setattr(orch, "run_ingest", lambda *a, **k: media)
    monkeypatch.setattr(orch, "run_transcribe", lambda *a, **k: _fake_transcript())
    monkeypatch.setattr(orch, "run_analyze", lambda *a, **k: [clip])
    # fake the face-tracked crop (fit layout, no detector needed)
    fit = CropPath.model_validate(
        {
            "layout": "fit",
            "source_width": 1280,
            "source_height": 720,
            "keyframes": [{"t": 0, "x": 0, "y": 0, "w": 1280, "h": 720}],
        }
    )
    monkeypatch.setattr(reframe_mod, "compute_crop_path", lambda *a, **k: fit)

    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = _job(settings)
    job.id = "jobx"
    # run synchronously
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED, final.error
    out = Path(final.clips[0].output_file or "")
    assert out.is_file()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "1080,1920" in probe.stdout and "h264" in probe.stdout
    thumb = Path(final.clips[0].thumbnail or "")
    assert thumb.is_file() and thumb.stat().st_size > 100
    preview = out.parent / "preview.mp4"
    assert preview.is_file()
    ass = out.parent / "captions.ass"
    assert ass.is_file()


def test_openapi_matches_docs() -> None:
    """The generated OpenAPI must equal docs/openapi.json (contract sync)."""
    from openclip.api.app import create_app

    docs = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    generated = create_app(Settings()).openapi()
    assert generated == json.loads(docs.read_text(encoding="utf-8"))
