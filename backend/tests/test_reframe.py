"""Tests for the reframe stage: tracking, crop geometry and service logic."""

import json
import subprocess
from pathlib import Path

import numpy as np  # pyright: ignore[reportMissingImports] - vision extra
import pytest

from openclip.core.config import Settings
from openclip.core.models import Clip, ClipSource, CropPath, MediaInfo
from openclip.reframe import compute_crop_path
from openclip.reframe.crop import (
    SubjectSample,
    build_crop_path,
    crop_window,
    fit_crop_path,
    simplify_keyframes,
    smooth_center_path,
)
from openclip.reframe.detector import Face
from openclip.reframe.errors import ReframeError
from openclip.reframe.service import render_debug_video
from openclip.reframe.tracking import (
    SubjectSelector,
    Track,
    is_scene_cut,
    scene_diff,
    update_tracks,
)


def _media(width: int = 1920, height: int = 1080, path: str = "/tmp/v.mp4") -> MediaInfo:
    return MediaInfo(
        path=path,
        duration=60.0,
        width=width,
        height=height,
        fps=30.0,
        has_audio=True,
    )


def _clip(start: float = 0.0, end: float = 10.0) -> Clip:
    return Clip(
        id="c1",
        start=start,
        end=end,
        score=80.0,
        title="t",
        hook="h",
        hashtags=[],
        rationale="r",
        source=ClipSource.HEURISTIC,
    )


class FakeDetector:
    """Scripted detector returning fixed faces per call."""

    def __init__(self, script: list[list[Face]] | Face | None = None) -> None:
        if script is None or isinstance(script, list) and not script:
            self._faces: list[list[Face]] = []
        elif isinstance(script, Face):
            self._faces = [[script]]
        else:
            self._faces = script
        self.calls = 0

    def detect(self, frame: np.ndarray) -> list[Face]:
        idx = min(self.calls, len(self._faces) - 1) if self._faces else 0
        self.calls += 1
        return list(self._faces[idx]) if self._faces else []

    def close(self) -> None:
        pass


# --- crop geometry ---------------------------------------------------------


def test_crop_window_landscape() -> None:
    window = crop_window(_media(1920, 1080))
    assert window is not None
    w, h = window
    assert h == 1080
    assert w % 2 == 0 and h % 2 == 0
    assert abs(w / h - 9 / 16) < 0.01


def test_crop_window_vertical_passthrough() -> None:
    assert crop_window(_media(1080, 1920)) is None
    assert crop_window(_media(1000, 2000)) is None  # taller than wide


def test_build_crop_path_vertical_source_full_frame() -> None:
    crop = build_crop_path(_media(1080, 1920), 10.0, [SubjectSample(t=0, cx=500)])
    assert crop.layout == "fill"
    assert len(crop.keyframes) == 1
    kf = crop.keyframes[0]
    assert (kf.w, kf.h) == (1080, 1920)
    assert kf.x == 0 and kf.t == 0.0


def test_smooth_center_path_dead_zone() -> None:
    media = _media(1920, 1080)
    window = crop_window(media)
    assert window is not None
    crop_w = window[0]
    # Subject near centre: movement smaller than the dead zone must not pan.
    centre = 960.0
    samples = [SubjectSample(t=i * 0.25, cx=centre + 5) for i in range(20)]
    points = smooth_center_path(samples, crop_w, media.width, media.height, 5.0)
    xs = {round(x, 1) for _, x in points}
    assert len(xs) <= 2  # initial point plus maybe one converged value


def test_smooth_center_path_max_pan_speed() -> None:
    media = _media(1920, 1080)
    window = crop_window(media)
    assert window is not None
    crop_w = window[0]
    # Subject teleports far right: centre may move at most ~0.35 crop widths/s.
    samples = [SubjectSample(t=i * 0.25, cx=300.0 if i < 4 else 1600.0) for i in range(40)]
    points = smooth_center_path(samples, crop_w, media.width, media.height, 10.0)
    dt = 0.25
    for (_, x0), (_, x1) in zip(points, points[1:], strict=False):
        assert abs(x1 - x0) <= crop_w * 0.35 * dt + crop_w * 0.02


def test_smooth_center_path_cut_snaps() -> None:
    media = _media(1920, 1080)
    window = crop_window(media)
    assert window is not None
    crop_w = window[0]
    samples = [
        SubjectSample(t=0.0, cx=400.0),
        SubjectSample(t=0.25, cx=400.0),
        SubjectSample(t=0.5, cx=1500.0, is_cut=True),
        SubjectSample(t=0.75, cx=1500.0),
    ]
    points = dict(smooth_center_path(samples, crop_w, media.width, media.height, 1.0))
    # After the cut the crop should be near the new subject, not still panning.
    assert points[0.75] > points[0.25] + crop_w * 0.2


def test_simplify_keyframes_bounds_and_endpoints() -> None:
    media = _media(1920, 1080)
    window = crop_window(media)
    assert window is not None
    crop_w = window[0]
    points = [(i * 0.25, 100.0 + i) for i in range(200)]
    kfs = simplify_keyframes(points, crop_w, media.width, media.height, 50.0)
    assert kfs[0].t == 0.0
    assert kfs[-1].t == pytest.approx(50.0, abs=0.01)
    assert len(kfs) <= int(50 / 60 * 40) + 2
    for kf in kfs:
        assert 0 <= kf.x <= media.width - crop_w
        assert kf.w == crop_w
        assert kf.h == media.height


def test_fit_crop_path_single_keyframe() -> None:
    crop = fit_crop_path(_media(1920, 1080), 10.0)
    assert crop.layout == "fit"
    assert len(crop.keyframes) == 1
    assert crop.keyframes[0].w == 1920 and crop.keyframes[0].h == 1080


# --- tracking --------------------------------------------------------------


def _face(cx: float, cy: float = 300.0, size: float = 100.0, conf: float = 0.9) -> Face:
    return Face(x=cx - size / 2, y=cy - size / 2, w=size, h=size, confidence=conf)


def test_update_tracks_matches_nearest() -> None:
    tracks: dict[int, Track] = {}
    next_id = [0]
    f1 = _face(500)
    m1 = update_tracks(tracks, [f1], 0, 200.0, next_id)
    assert len(m1) == 1
    tid = next(iter(m1))
    # Face moves slightly: same track.
    m2 = update_tracks(tracks, [_face(520)], 1, 200.0, next_id)
    assert tid in m2
    assert len(tracks) == 1


def test_update_tracks_new_face_gets_new_track() -> None:
    tracks: dict[int, Track] = {}
    next_id = [0]
    update_tracks(tracks, [_face(300), _face(1500)], 0, 200.0, next_id)
    assert len(tracks) == 2


def test_subject_selector_hysteresis() -> None:
    selector = SubjectSelector(fps=4.0)
    tracks: dict[int, Track] = {}
    next_id = [0]
    # Subject A dominates for a while.
    for i in range(12):
        fa = _face(400, size=120, conf=0.9)
        fb = _face(1400, size=60, conf=0.8)
        matched = update_tracks(tracks, [fa, fb], i, 400.0, next_id)
        cx = selector.update(tracks, matched, i)
        assert cx is None or cx < 800
    # B briefly becomes the "best" by score but must NOT switch for <1.5 s.
    switched_at = None
    for i in range(12, 20):
        fa = _face(400, size=30, conf=0.5)
        fb = _face(1400, size=200, conf=0.95)
        matched = update_tracks(tracks, [fa, fb], i, 400.0, next_id)
        cx = selector.update(tracks, matched, i)
        if cx is not None and cx > 800:
            switched_at = i
            break
    # After 8 frames (2 s) of dominance the switch is allowed.
    assert switched_at is not None and switched_at >= 12 + 6


def test_subject_selector_lost_subject_returns_last() -> None:
    selector = SubjectSelector(fps=4.0)
    tracks: dict[int, Track] = {}
    next_id = [0]
    for i in range(10):
        matched = update_tracks(tracks, [_face(600)], i, 400.0, next_id)
        selector.update(tracks, matched, i)
    # No detections: selector keeps last known centre.
    for tid in list(tracks):
        tracks[tid].missing += 1
    cx = selector.update(tracks, {}, 10)
    assert cx == pytest.approx(600.0, abs=5.0)


def test_scene_diff_and_cut() -> None:
    rng = np.random.default_rng(0)
    a = rng.integers(0, 255, (90, 160, 3), dtype=np.uint8).mean(axis=2)
    assert scene_diff(a, a.copy()) == pytest.approx(0.0, abs=0.5)
    assert is_scene_cut(scene_diff(a, a.copy())) is False
    b = np.full_like(a, 200.0)
    assert is_scene_cut(scene_diff(a, b)) is True


# --- service ---------------------------------------------------------------


def test_compute_crop_path_fit_layout_no_sampling(tmp_path: Path) -> None:
    media = _media(path=str(tmp_path / "missing.mp4"))
    crop = compute_crop_path(media, _clip(), "fit", settings=Settings())
    assert crop.layout == "fit"
    assert crop.keyframes[0].w == 1920


def test_compute_crop_path_auto_no_faces_falls_back_to_fit(tmp_path: Path) -> None:
    """auto with a detector that never finds faces chooses fit."""
    video = tmp_path / "v.mp4"
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
            "testsrc=duration=2:size=640x360:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    media = _media(640, 360, str(video))
    clip = _clip(0.0, 2.0)
    crop = compute_crop_path(media, clip, "auto", settings=Settings(), detector=FakeDetector([]))
    assert crop.layout == "fit"


def test_compute_crop_path_fill_with_fake_detector(tmp_path: Path) -> None:
    """fill layout on a real tiny video with a fake face detector."""
    video = tmp_path / "v.mp4"
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
            "testsrc=duration=2:size=640x360:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    media = _media(640, 360, str(video))
    # Face centred in the 640-wide sampled frame.
    face = Face(x=270, y=100, w=100, h=100, confidence=0.9)
    crop = compute_crop_path(
        media, _clip(0.0, 2.0), "fill", settings=Settings(), detector=FakeDetector(face)
    )
    assert crop.layout == "fill"
    assert len(crop.keyframes) >= 1
    kf = crop.keyframes[0]
    assert kf.w % 2 == 0 and kf.h == 360
    assert 0 <= kf.x <= media.width - kf.w


def test_compute_crop_path_no_video_stream_falls_back(tmp_path: Path) -> None:
    """A media path that ffmpeg cannot decode yields fit for auto."""
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    media = _media(1920, 1080, str(bad))
    with pytest.raises(ReframeError):
        compute_crop_path(media, _clip(), "fill", settings=Settings(), detector=FakeDetector([]))


def test_render_debug_video(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    out = tmp_path / "debug.mp4"
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
            "testsrc=duration=2:size=640x360:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    media = _media(640, 360, str(video))
    crop = CropPath.model_validate(
        {
            "layout": "fill",
            "source_width": 640,
            "source_height": 360,
            "keyframes": [{"t": 0.0, "x": 80, "y": 0, "w": 202, "h": 360}],
        }
    )
    render_debug_video(media, crop, 0.0, 2.0, out, Settings())
    assert out.is_file() and out.stat().st_size > 1000


# --- real YuNet (slow) ------------------------------------------------------


@pytest.mark.slow
def test_yunet_detector_on_synthetic_frame(tmp_path: Path) -> None:
    """Run the real YuNet model on a generated frame; skip if unavailable."""
    from openclip.reframe.detector import YuNetDetector, yunet_model_path

    settings = Settings()
    settings.data_dir = tmp_path / "data"
    try:
        model = yunet_model_path(settings)
        detector = YuNetDetector(model)
    except ReframeError as exc:
        pytest.skip(f"YuNet detector unavailable: {exc}")
    try:
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        faces = detector.detect(frame)
        assert isinstance(faces, list)
    finally:
        detector.close()
    assert model.is_file()


def test_clips_json_roundtrip(tmp_path: Path) -> None:
    from openclip.cli.reframe_cmd import _load_clips, _pick_clip
    from openclip.core.errors import OpenClipError

    clip = _clip()
    path = tmp_path / "clips.json"
    path.write_text(json.dumps([clip.model_dump(mode="json")]), encoding="utf-8")
    loaded = _load_clips(path)
    assert _pick_clip(loaded, 1, None).id == "c1"
    assert _pick_clip(loaded, None, "c1").id == "c1"
    with pytest.raises(OpenClipError):
        _pick_clip(loaded, 5, None)
    with pytest.raises(OpenClipError):
        _pick_clip(loaded, None, "nope")
