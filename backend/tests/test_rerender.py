"""Regression and integration tests for clip re-render (PATCH) and transcript cache."""

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openclip.core.models import (
    Clip,
    ClipSource,
    Job,
    JobOptions,
    JobSource,
    JobStatus,
    MediaInfo,
    Segment,
    Transcript,
    Word,
)
from openclip.pipeline import store
from openclip.pipeline.orchestrator import Pipeline
from openclip.transcribe.cache import load_cached_transcript, save_cached

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None, reason="ffmpeg/ffprobe not installed"
)


def _settings(tmp_path: Path):
    from openclip.core.config import Settings

    settings = Settings()
    settings.data_dir = tmp_path / "data"
    return settings


def _wordy_transcript(duration: float = 20.0) -> Transcript:
    """Transcript with speech-like words across the whole source."""
    segments: list[Segment] = []
    vocab = ["this", "is", "the", "part", "that", "really", "matters", "alright"]
    seg_id = 0
    t = 0.0
    while t < duration - 1.0:
        seg_end = min(t + 5.0, duration)
        words: list[Word] = []
        wt = t
        i = 0
        while wt < seg_end - 0.2:
            wend = min(wt + 0.35, seg_end)
            words.append(Word(text=vocab[i % len(vocab)], start=wt, end=wend, probability=0.9))
            wt = wend
            i += 1
        text = " ".join(w.text for w in words)
        segments.append(Segment(id=seg_id, start=t, end=seg_end, text=text, words=words))
        seg_id += 1
        t = seg_end
    return Transcript(language="en", duration=duration, model="fake", segments=segments)


def _make_video(path: Path) -> None:
    subprocess.run(
        [
            FFMPEG or "ffmpeg",
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
            str(path),
        ],
        check=True,
    )


def _frame(video: Path, t: float, out: Path) -> Path:
    subprocess.run(
        [
            FFMPEG or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{t:.2f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
    )
    return out


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            FFPROBE or "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            FFPROBE or "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    w, h = out.stdout.strip().split(",")[:2]
    return int(w), int(h)


def _captionless_ass(captions_ass: Path, out: Path) -> Path:
    """Same ASS styling with every Dialogue event stripped -> no captions."""
    lines = [
        line
        for line in captions_ass.read_text(encoding="utf-8").splitlines()
        if not line.startswith("Dialogue:")
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# --- transcript cache envelope (bug 3a regression) -------------------------


def test_load_cached_transcript_reads_envelope(tmp_path: Path) -> None:
    transcript = _wordy_transcript(5.0)
    save_cached(tmp_path, "key-1", transcript)
    loaded = load_cached_transcript(tmp_path)
    assert loaded is not None
    assert loaded.language == transcript.language
    assert len(loaded.segments) == len(transcript.segments)


def test_load_cached_transcript_orchestrator_format(tmp_path: Path) -> None:
    """The orchestrator writes ``{"transcript": ...}`` without a cache_key."""
    transcript = _wordy_transcript(5.0)
    (tmp_path / "transcript.json").write_text(
        json.dumps({"transcript": transcript.model_dump(mode="json")})
    )
    loaded = load_cached_transcript(tmp_path)
    assert loaded is not None and len(loaded.segments) == len(transcript.segments)


def test_load_cached_transcript_missing_and_garbage(tmp_path: Path) -> None:
    assert load_cached_transcript(tmp_path) is None
    (tmp_path / "transcript.json").write_text("{not json")
    assert load_cached_transcript(tmp_path) is None
    (tmp_path / "transcript.json").write_text(json.dumps({"transcript": {"bad": 1}}))
    assert load_cached_transcript(tmp_path) is None


# --- PATCH integration on a real render -------------------------------------


@pytest.fixture(scope="module")
def rerender_env(tmp_path_factory):
    """Completed job with real rendered artifacts on a 20 s lavfi video."""
    mp = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("rerender")
    pytest.importorskip("numpy")
    if FFMPEG is None or FFPROBE is None:
        pytest.skip("ffmpeg/ffprobe not installed")

    monkeypatch = mp
    import openclip.pipeline.orchestrator as orch
    import openclip.reframe as reframe_mod
    from openclip.api.app import create_app
    from openclip.core.models import CropPath

    video = tmp_path / "src20.mp4"
    _make_video(video)
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
    transcript = _wordy_transcript(20.0)
    fit = CropPath.model_validate(
        {
            "layout": "fit",
            "source_width": 1280,
            "source_height": 720,
            "keyframes": [{"t": 0, "x": 0, "y": 0, "w": 1280, "h": 720}],
        }
    )
    monkeypatch.setattr(orch, "run_ingest", lambda *a, **k: media)
    monkeypatch.setattr(orch, "run_transcribe", lambda *a, **k: transcript)
    monkeypatch.setattr(orch, "run_analyze", lambda *a, **k: [clip])
    monkeypatch.setattr(reframe_mod, "compute_crop_path", lambda *a, **k: fit)

    settings = _settings(tmp_path)
    pipe = Pipeline(settings)
    job = Job(
        id=store.new_job_id(),
        status=JobStatus.QUEUED,
        progress=0.0,
        source=JobSource(type="upload", value=str(video)),
        options=JobOptions(min_clip_seconds=1.0, max_clip_seconds=60.0),
        created_at=store.utcnow(),
        updated_at=store.utcnow(),
    )
    final = pipe.execute(job)
    assert final.status == JobStatus.COMPLETED, final.error

    clip_dir = Path(final.clips[0].output_file or "").parent
    assert clip_dir.is_dir()
    assert (clip_dir / "clip.mp4").is_file()
    assert (clip_dir / "preview.mp4").is_file()
    assert (clip_dir / "thumbnail.jpg").is_file()
    assert (clip_dir / "captions.ass").is_file()

    app = create_app(settings, pipeline=pipe)
    client = TestClient(app)
    yield {
        "client": client,
        "pipe": pipe,
        "settings": settings,
        "job": final,
        "clip": final.clips[0],
        "clip_dir": clip_dir,
        "tmp": tmp_path,
        "media": media,
        "transcript": transcript,
    }
    mp.undo()


@requires_ffmpeg
def test_patch_style_change_produces_new_captioned_render(rerender_env) -> None:
    env = rerender_env
    client: TestClient = env["client"]
    pipe: Pipeline = env["pipe"]
    job: Job = env["job"]
    clip: Clip = env["clip"]
    clip_dir: Path = env["clip_dir"]
    tmp: Path = env["tmp"]

    old_clip = clip_dir / "clip.mp4"
    old_bytes = _md5(old_clip)
    old_frame = _md5(_frame(old_clip, 2.0, tmp / "old.png"))

    # subscribe before PATCH so we catch the clip_ready frame
    q = pipe.bus.subscribe(job.id)
    try:
        resp = client.patch(
            f"/api/jobs/{job.id}/clips/{clip.id}",
            json={"caption_style_id": "bold-impact"},
        )
        assert resp.status_code == 200

        deadline = time.time() + 180
        saw_ready = False
        seen: list[str] = []
        while time.time() < deadline and not saw_ready:
            try:
                frame = q.get(timeout=1.0)
            except Exception:
                continue
            seen.append(frame)
            if "event: clip_ready" in frame:
                saw_ready = True
                # envelope: {job_id, clip}
                payload = json.loads(frame.split("data:", 1)[1])
                assert payload["job_id"] == job.id
                assert payload["clip"]["id"] == clip.id
        assert saw_ready, f"no clip_ready SSE frame after PATCH; saw: {seen[-5:]}"
    finally:
        pipe.bus.unsubscribe(job.id, q)

    assert _md5(old_clip) != old_bytes, "clip.mp4 was not re-encoded"
    new_frame = _md5(_frame(old_clip, 2.0, tmp / "new.png"))
    assert new_frame != old_frame, "extracted frame identical -> style change not rendered"

    # preview is low-res but must carry the same burned-in captions
    w, h = _probe_size(clip_dir / "preview.mp4")
    assert (w, h) == (360, 640)
    cap_frame = _frame(clip_dir / "preview.mp4", 2.0, tmp / "prev_cap.png")
    # captionless reference at the same size
    from openclip.core import models
    from openclip.render.service import render_clip, render_preview

    workdir = store.job_dir(env["settings"], job.id)
    crop = models.CropPath.model_validate_json((workdir / f"crop-{clip.id}.json").read_text())
    media = env["media"]
    captionless = tmp / "preview_nocap.mp4"
    render_preview(media, clip, crop, captionless, ass_path=None, settings=env["settings"])
    nocap_frame = _frame(captionless, 2.0, tmp / "prev_nocap.png")
    assert _md5(cap_frame) != _md5(nocap_frame), "preview has no visible captions"

    # final render frame must differ from a captionless full render too
    empty_ass = _captionless_ass(clip_dir / "captions.ass", tmp / "empty.ass")
    nocap_full = tmp / "clip_nocap.mp4"
    render_clip(
        media,
        clip,
        crop,
        empty_ass,
        nocap_full,
        job.options,
        settings=env["settings"],
    )
    f_cap = _frame(old_clip, 2.0, tmp / "f_cap.png")
    f_nocap = _frame(nocap_full, 2.0, tmp / "f_nocap.png")
    assert _md5(f_cap) != _md5(f_nocap), "final render shows no captions"


@requires_ffmpeg
def test_patch_trim_updates_bounds_and_duration(rerender_env) -> None:
    env = rerender_env
    client: TestClient = env["client"]
    pipe: Pipeline = env["pipe"]
    job: Job = env["job"]
    clip: Clip = env["clip"]
    clip_dir: Path = env["clip_dir"]

    q = pipe.bus.subscribe(job.id)
    try:
        resp = client.patch(
            f"/api/jobs/{job.id}/clips/{clip.id}",
            json={"start": 4.0, "end": 8.0},
        )
        assert resp.status_code == 200
        assert resp.json()["start"] == 4.0 and resp.json()["end"] == 8.0
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                frame = q.get(timeout=1.0)
            except Exception:
                continue
            if "event: clip_ready" in frame:
                break
        else:
            pytest.fail("no clip_ready after trim PATCH")
    finally:
        pipe.bus.unsubscribe(job.id, q)

    duration = _probe_duration(clip_dir / "clip.mp4")
    assert duration == pytest.approx(4.0, abs=0.6)
    w, h = _probe_size(clip_dir / "clip.mp4")
    assert (w, h) == (1080, 1920)


@requires_ffmpeg
def test_patch_conflict_returns_409(rerender_env) -> None:
    env = rerender_env
    client: TestClient = env["client"]
    pipe: Pipeline = env["pipe"]
    job: Job = env["job"]
    clip: Clip = env["clip"]

    lock = pipe.clip_rerender_lock(job.id, clip.id)
    assert lock.acquire(blocking=False)
    try:
        resp = client.patch(
            f"/api/jobs/{job.id}/clips/{clip.id}",
            json={"caption_style_id": "minimal-clean"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "rerender_in_progress"
    finally:
        lock.release()


@requires_ffmpeg
def test_patch_validation_errors(rerender_env) -> None:
    env = rerender_env
    client: TestClient = env["client"]
    job: Job = env["job"]
    clip: Clip = env["clip"]
    url = f"/api/jobs/{job.id}/clips/{clip.id}"

    # end <= start
    resp = client.patch(url, json={"start": 5.0, "end": 5.0})
    assert resp.status_code == 422 and resp.json()["code"] == "invalid_range"
    # length outside min/max (job allows 1-60 s)
    resp = client.patch(url, json={"start": 0.0, "end": 120.0})
    assert resp.status_code == 422 and resp.json()["code"] in (
        "invalid_duration",
        "out_of_range",
    )
    # bounds outside the 20 s source
    resp = client.patch(url, json={"start": 15.0, "end": 25.0})
    assert resp.status_code == 422
    # unknown style
    resp = client.patch(url, json={"caption_style_id": "no-such-style"})
    assert resp.status_code == 422 and resp.json()["code"] == "unknown_style"


@requires_ffmpeg
def test_failed_rerender_keeps_old_files(rerender_env, monkeypatch) -> None:
    env = rerender_env
    client: TestClient = env["client"]
    pipe: Pipeline = env["pipe"]
    job: Job = env["job"]
    clip: Clip = env["clip"]
    clip_dir: Path = env["clip_dir"]

    before = {name: _md5(clip_dir / name) for name in ("clip.mp4", "preview.mp4", "thumbnail.jpg")}

    import openclip.pipeline.orchestrator as orch

    def boom(*a, **k):
        raise RuntimeError("forced render failure")

    monkeypatch.setattr(orch, "_render_clip_artifacts", boom)

    q = pipe.bus.subscribe(job.id)
    try:
        resp = client.patch(
            f"/api/jobs/{job.id}/clips/{clip.id}",
            json={"caption_style_id": "karaoke-glow"},
        )
        assert resp.status_code == 200
        deadline = time.time() + 60
        saw_error = False
        while time.time() < deadline and not saw_error:
            try:
                frame = q.get(timeout=1.0)
            except Exception:
                continue
            if "event: error" in frame and clip.id in frame:
                saw_error = True
        assert saw_error, "no clip-scoped error event"
    finally:
        pipe.bus.unsubscribe(job.id, q)

    for name, digest in before.items():
        assert _md5(clip_dir / name) == digest, f"{name} was replaced despite failure"
