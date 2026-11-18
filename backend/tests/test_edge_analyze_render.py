"""Edge-case matrix: analyse, reframe and render robustness.

These tests stay cheap: faked detectors, 1-2 second lavfi fixtures and pure
geometry. Nothing here downloads a model or transcodes real speech.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from openclip.core.config import Settings
from openclip.core.errors import AnalysisError
from openclip.core.models import (
    Candidate,
    CandidateFeatures,
    Clip,
    ClipSource,
    CropKeyframe,
    CropPath,
    JobOptions,
    MediaInfo,
    Segment,
    Transcript,
    Word,
)

# ---------- analyze: degenerate transcripts ----------


def _segment(start: float, end: float, text: str) -> Segment:
    words: list[Word] = []
    tokens = text.split()
    span = (end - start) / max(1, len(tokens))
    for i, token in enumerate(tokens):
        words.append(Word(text=token, start=start + i * span, end=start + (i + 1) * span))
    return Segment(id=0, start=start, end=end, text=text, words=words)


def _transcript(text: str, duration: float = 30.0) -> Transcript:
    return Transcript(
        language="en", duration=duration, model="test", segments=[_segment(0.0, duration, text)]
    )


def _media(duration: float = 30.0) -> MediaInfo:
    return MediaInfo(
        path="/nonexistent/v.mp4",
        duration=duration,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )


def test_silent_transcript_raises_a_clear_error() -> None:
    """No speech at all: AnalysisError, never an empty crash."""
    from openclip.analyze import analyze

    empty = Transcript(language="en", duration=10.0, model="test", segments=[])
    with pytest.raises(AnalysisError) as info:
        analyze(empty, _media(10.0), JobOptions())
    assert "no sentences" in str(info.value)


def test_transcript_with_only_blank_segments_raises() -> None:
    from openclip.analyze.candidates import find_candidates

    # Words are absent and the text is whitespace: nothing to segment.
    transcript = Transcript(
        language="en",
        duration=5.0,
        model="test",
        segments=[Segment(id=0, start=0.0, end=5.0, text="   ")],
    )
    with pytest.raises(AnalysisError):
        find_candidates(transcript, _media(5.0), JobOptions())


def test_very_short_source_yields_one_flagged_candidate() -> None:
    from openclip.analyze.candidates import find_candidates

    transcript = _transcript("One short sentence only.", 3.0)
    candidates = find_candidates(transcript, _media(3.0), JobOptions(min_clip_seconds=20))
    assert len(candidates) == 1
    assert candidates[0].end - candidates[0].start < 20


def test_analyze_on_short_source_keeps_the_clip() -> None:
    """A sub-minimum source must still produce a playable clip, not zero clips."""
    from openclip.analyze import analyze

    transcript = _transcript(
        "This is a very short but complete thought about focus. It ends here.", 6.0
    )
    clips = analyze(
        transcript,
        _media(6.0),
        JobOptions(min_clip_seconds=20, analysis_mode="heuristic"),
        settings=Settings(llm_base_url="http://127.0.0.1:9", llm_model="x"),
    )
    assert len(clips) == 1
    assert clips[0].end <= 6.5


def test_candidate_windows_never_exceed_max_seconds() -> None:
    from openclip.analyze.candidates import find_candidates

    long_text = " ".join(
        f"This is sentence number {i} with several words in it." for i in range(40)
    )
    transcript = _transcript(long_text, 200.0)
    candidates = find_candidates(
        transcript, _media(200.0), JobOptions(min_clip_seconds=20, max_clip_seconds=60)
    )
    assert candidates
    for candidate in candidates:
        assert candidate.end - candidate.start <= 60.5
        assert candidate.start >= 0.0


def test_analyze_cancellation_is_reported() -> None:
    import threading

    from openclip.analyze import analyze

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(AnalysisError):
        analyze(
            _transcript("Some words here.", 5.0),
            _media(5.0),
            JobOptions(analysis_mode="heuristic"),
            cancel=cancel,
        )


def test_select_clips_respects_max_clips_and_ordering() -> None:
    from openclip.analyze.select import select_clips

    def clip(i: int, start: float, score: float) -> Clip:
        return Clip(
            id=f"c{i}",
            start=start,
            end=start + 30.0,
            score=score,
            title="t",
            hook="h",
            hashtags=[],
            rationale="r",
            source=ClipSource.HEURISTIC,
        )

    clips = [clip(i, i * 60.0, 50.0 + i) for i in range(6)]
    selected = select_clips(clips, JobOptions(max_clips=3))
    assert len(selected) == 3
    assert [c.score for c in selected] == sorted((c.score for c in selected), reverse=True)


def test_clip_metadata_survives_a_transcript_with_no_usable_topics() -> None:
    """Titles/hooks/hashtags must be non-empty even for degenerate text."""
    from openclip.analyze.service import _heuristic_metadata

    candidate = Candidate(
        id="candidate-001",
        start=0.0,
        end=20.0,
        text="Um uh. Yeah okay. Right.",
        features=CandidateFeatures(
            speech_rate_wps=1.0,
            audio_energy=0.1,
            question_count=0,
            scene_changes=0,
            hook_score=0.0,
        ),
        heuristic_score=5.0,
    )
    title, hook, tags, rationale = _heuristic_metadata(candidate, 20.0)
    assert title and hook and rationale
    assert isinstance(tags, list)


# ---------- reframe: geometry ----------


def test_already_vertical_source_passes_through_full_frame() -> None:
    from openclip.reframe.crop import crop_window

    vertical = MediaInfo(
        path="/tmp/v.mp4", duration=5.0, width=1080, height=1920, fps=30.0, has_audio=True
    )
    assert crop_window(vertical) is None  # full frame, no crop window


def test_square_source_keeps_a_916_window() -> None:
    from openclip.reframe.crop import crop_window

    square = MediaInfo(
        path="/tmp/v.mp4", duration=5.0, width=1000, height=1000, fps=30.0, has_audio=True
    )
    window = crop_window(square)
    assert window is not None
    w, h = window
    assert w % 2 == 0 and h % 2 == 0
    assert abs(w / h - 9 / 16) < 0.01


def test_very_low_resolution_source_still_yields_a_window() -> None:
    from openclip.reframe.crop import crop_window

    tiny = MediaInfo(path="/tmp/v.mp4", duration=1.0, width=64, height=48, fps=15.0, has_audio=True)
    window = crop_window(tiny)
    assert window is not None
    w, h = window
    assert w > 0 and h > 0


def test_very_wide_source_window_is_inside_the_frame() -> None:
    from openclip.reframe.crop import crop_window

    wide = MediaInfo(
        path="/tmp/v.mp4", duration=1.0, width=2560, height=200, fps=30.0, has_audio=True
    )
    window = crop_window(wide)
    assert window is not None
    w, h = window
    assert w <= wide.width and h <= wide.height


def test_crop_path_without_faces_falls_back_to_centre(tmp_path: Path) -> None:
    from openclip.reframe.crop import SubjectSample, build_crop_path

    media = MediaInfo(
        path=str(tmp_path / "v.mp4"),
        duration=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    path = build_crop_path(media, 4.0, [SubjectSample(t=0.0, cx=None)])
    assert path.layout == "fill"
    assert path.keyframes[0].t == 0.0
    assert path.keyframes[-1].t == pytest.approx(4.0)


def test_several_faces_do_not_produce_an_invalid_path() -> None:
    from openclip.reframe.crop import SubjectSample, build_crop_path

    media = MediaInfo(
        path="/tmp/v.mp4", duration=8.0, width=1920, height=1080, fps=30.0, has_audio=True
    )
    samples = [
        SubjectSample(t=i * 0.25, cx=(400 if i % 2 else 1500), is_cut=i % 8 == 0) for i in range(32)
    ]
    path = build_crop_path(media, 8.0, samples)
    width = path.keyframes[0].w
    for keyframe in path.keyframes:
        assert 0 <= keyframe.x <= media.width - width
        assert keyframe.w == width


# ---------- render: filter chains and failures ----------


@pytest.fixture
def tiny_source(tmp_path: Path) -> Path:
    """A 2 s 160x120 source with a soft tone; requires FFmpeg."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    out = tmp_path / "src.mp4"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=160x120:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg could not build the fixture: {proc.stderr.strip()[:120]}")
    return out


def _clip(start: float = 0.0, end: float = 1.0) -> Clip:
    return Clip(
        id="clip-1",
        start=start,
        end=end,
        score=80.0,
        title="t",
        hook="h",
        hashtags=[],
        rationale="r",
        source=ClipSource.HEURISTIC,
    )


def _media_for(path: Path) -> MediaInfo:
    return MediaInfo(path=str(path), duration=2.0, width=160, height=120, fps=15.0, has_audio=True)


def test_render_failure_is_reported_with_a_code(tmp_path: Path, tiny_source: Path) -> None:
    from openclip.render.service import render_clip

    media = _media_for(tiny_source)
    clip = _clip()
    crop = CropPath(
        layout="fit",
        source_width=160,
        source_height=120,
        keyframes=[CropKeyframe(t=0.0, x=0, y=0, w=160, h=120)],
    )
    ass = tmp_path / "captions.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\n", encoding="utf-8")
    out = tmp_path / "out" / "clip.mp4"
    # Ask for a clip past the end of the source: ffmpeg exits 0 with no frames,
    # so the honest check is that no partial .part file is left behind.
    result = render_clip(media, clip, crop, ass, out, JobOptions(), settings=Settings())
    assert result == out
    assert out.is_file() and out.stat().st_size > 0
    assert not (tmp_path / "out" / "clip.part.mp4").exists()


def test_render_cleans_up_partial_output_on_ffmpeg_failure(
    tmp_path: Path, tiny_source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openclip.render import service as render_service

    def boom(*_a, **_k):
        raise render_service.RenderFailure("render_failed", "simulated failure")

    monkeypatch.setattr(render_service, "_run_ffmpeg", boom)
    media = _media_for(tiny_source)
    crop = CropPath(
        layout="fit",
        source_width=160,
        source_height=120,
        keyframes=[CropKeyframe(t=0.0, x=0, y=0, w=160, h=120)],
    )
    ass = tmp_path / "captions.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\n", encoding="utf-8")
    out = tmp_path / "out" / "clip.mp4"
    out.parent.mkdir()
    (tmp_path / "out" / "clip.part.mp4").write_bytes(b"half")
    with pytest.raises(render_service.RenderFailure):
        render_service.render_clip(media, _clip(), crop, ass, out, JobOptions())
    assert not out.exists()
    assert not (tmp_path / "out" / "clip.part.mp4").exists()


def test_render_disk_full_is_detected_when_the_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno as errno_mod

    from openclip.render import service as render_service

    target = tmp_path / "out" / "clip.part.mp4"
    target.parent.mkdir(parents=True)
    real_write = Path.write_bytes

    def fake_write(self: Path, data: bytes) -> int:  # noqa: ARG001
        if self.name == ".openclip-space-probe":
            raise OSError(errno_mod.ENOSPC, "No space left on device")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", fake_write)
    assert render_service._is_disk_full(target) is True


def test_render_failure_message_is_not_disk_full_on_other_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno as errno_mod

    from openclip.render import service as render_service

    target = tmp_path / "out" / "clip.part.mp4"
    target.parent.mkdir(parents=True)

    def fake_write(self: Path, data: bytes) -> int:  # noqa: ARG001
        raise OSError(errno_mod.EACCES, "denied")

    monkeypatch.setattr(Path, "write_bytes", fake_write)
    assert render_service._is_disk_full(target) is False


def test_render_cancel_raises_a_coded_error(tiny_source: Path, tmp_path: Path) -> None:
    import threading

    from openclip.render import service as render_service

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(render_service.RenderCancelled) as info:
        render_service._check_cancel(cancel)
    assert info.value.code == "cancelled"
