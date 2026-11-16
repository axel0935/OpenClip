"""Reframe stage orchestration: sampling, tracking, crop path, debug video."""

import logging
import subprocess
import threading
from pathlib import Path
from typing import Literal

import numpy as np  # pyright: ignore[reportMissingImports] - vision extra

from openclip.core.config import Settings
from openclip.core.models import Clip, CropKeyframe, CropPath, MediaInfo
from openclip.reframe.crop import (
    SubjectSample,
    build_crop_path,
    crop_window,
    fit_crop_path,
)
from openclip.reframe.detector import Face, FaceDetector, YuNetDetector, yunet_model_path
from openclip.reframe.errors import ReframeError
from openclip.reframe.sampler import ProgressCallback, iter_clip_frames
from openclip.reframe.tracking import (
    SubjectSelector,
    Track,
    is_scene_cut,
    scene_diff,
    update_tracks,
)

log = logging.getLogger(__name__)

LayoutChoice = Literal["auto", "fill", "fit"]

_FACE_COVERAGE_MIN = 0.60


def _build_detector(settings: Settings) -> FaceDetector | None:
    """Create a YuNet detector, or ``None`` when vision deps are unavailable."""
    try:
        return YuNetDetector(yunet_model_path(settings))
    except ReframeError as exc:
        if exc.code == "vision_unavailable":
            log.warning("Face detection unavailable: %s", exc)
            return None
        raise


def _sample_subjects(
    media: MediaInfo,
    clip: Clip,
    detector: FaceDetector | None,
    settings: Settings,
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> tuple[list[SubjectSample], int, int]:
    """Sample the clip, track faces and return subject-x observations."""
    tracks: dict[int, Track] = {}
    selector = SubjectSelector()
    next_id = [0]
    samples: list[SubjectSample] = []
    total = 0
    with_faces = 0
    prev_gray: np.ndarray | None = None
    max_dist = media.width * 0.10

    for frame in iter_clip_frames(
        media, clip.start, clip.end, settings, progress=progress, cancel=cancel
    ):
        total += 1
        detections = detector.detect(frame.image) if detector is not None else []
        # Map frame pixels back to source pixels.
        scale_x = media.width / frame.image.shape[1]
        scale_y = media.height / frame.image.shape[0]
        src_faces = [
            Face(
                x=d.x * scale_x,
                y=d.y * scale_y,
                w=d.w * scale_x,
                h=d.h * scale_y,
                confidence=d.confidence,
            )
            for d in detections
        ]
        cur_gray = frame.image.mean(axis=2)
        diff = scene_diff(prev_gray, cur_gray) if prev_gray is not None else 0.0
        prev_gray = cur_gray
        cut = 1.0 if is_scene_cut(diff) else 0.0
        matched = update_tracks(tracks, src_faces, total - 1, max_dist, next_id)
        cx = selector.update(tracks, matched, total - 1)
        if cx is not None:
            with_faces += 1
        samples.append(SubjectSample(t=frame.t, cx=cx, is_cut=cut > 0))
    return samples, with_faces, total


def compute_crop_path(
    media: MediaInfo,
    clip: Clip,
    layout: LayoutChoice = "auto",
    *,
    settings: Settings | None = None,
    detector: FaceDetector | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> CropPath:
    """Compute the vertical crop path for a clip (contract signature + kwargs)."""
    settings = settings or Settings()
    clip_duration = max(0.0, clip.end - clip.start)
    choice = layout
    reason = ""

    if choice == "fit":
        return fit_crop_path(media, clip_duration)

    owns_detector = detector is None
    if detector is None:
        detector = _build_detector(settings)

    try:
        if detector is None:
            if choice == "auto":
                log.info("auto layout: no face detector available -> fit")
                return fit_crop_path(media, clip_duration)
            # Explicit fill without a detector: calm centred crop.
            return build_crop_path(
                media,
                clip_duration,
                [
                    SubjectSample(t=0.0, cx=None),
                    SubjectSample(t=clip_duration, cx=None),
                ],
            )

        try:
            samples, with_faces, total = _sample_subjects(
                media, clip, detector, settings, progress, cancel
            )
        except ReframeError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReframeError("no_video_stream", f"Video sampling failed: {exc}") from exc

        coverage = with_faces / total if total else 0.0
        if choice == "auto":
            window = crop_window(media)
            travel_ok = True
            if window is not None and samples:
                xs = [s.cx for s in samples if s.cx is not None]
                if xs:
                    # Subject must stay reachable by the pan-speed-limited path.
                    travel_ok = (max(xs) - min(xs)) <= media.width * 0.5
            if coverage >= _FACE_COVERAGE_MIN and travel_ok:
                choice = "fill"
                reason = f"auto->fill: face coverage {coverage:.0%}"
            else:
                choice = "fit"
                reason = f"auto->fit: face coverage {coverage:.0%} (need {_FACE_COVERAGE_MIN:.0%})"
                log.info("Layout decision: %s", reason)
                return fit_crop_path(media, clip_duration)
        else:
            reason = "explicit fill"
            log.info("Layout decision: fill (%s, coverage %.0f%%)", reason, coverage * 100)

        return build_crop_path(media, clip_duration, samples)
    finally:
        if owns_detector and detector is not None:
            detector.close()


def render_debug_video(
    media: MediaInfo,
    crop: CropPath,
    clip_start: float,
    clip_end: float,
    out_path: Path,
    settings: Settings | None = None,
    *,
    max_seconds: float = 30.0,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Render a short downscaled preview with the crop rectangle drawn."""
    settings = settings or Settings()
    duration = min(max_seconds, max(0.5, clip_end - clip_start))
    out_width = 360
    height = int(media.height * out_width / media.width)
    height -= height % 2
    if height < 2:
        raise ReframeError("invalid_media", "Cannot determine debug video size.")
    sx = out_width / media.width

    def _keyframe_at(t: float):
        kfs = sorted(crop.keyframes, key=lambda k: k.t)
        if not kfs:
            return None
        if t <= kfs[0].t:
            return kfs[0]
        for a, b in zip(kfs, kfs[1:], strict=False):
            if a.t <= t <= b.t:
                f = 0.0 if b.t == a.t else (t - a.t) / (b.t - a.t)
                return CropKeyframe(
                    t=t,
                    x=int(round(a.x + (b.x - a.x) * f)),
                    y=int(round(a.y + (b.y - a.y) * f)),
                    w=int(round(a.w + (b.w - a.w) * f)),
                    h=int(round(a.h + (b.h - a.h) * f)),
                )
        return kfs[-1]

    # Two ffmpeg processes: decode -> numpy draw -> encode.
    dec_cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{clip_start:.3f}",
        "-i",
        media.path,
        "-t",
        f"{duration:.3f}",
        "-vf",
        f"fps=10,scale={out_width}:-2",
        "-an",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-",
    ]
    enc_cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{out_width}x{height}",
        "-r",
        "10",
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        str(out_path),
    ]
    try:
        dec = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise ReframeError("ffmpeg_missing", f"ffmpeg '{settings.ffmpeg_path}' missing.") from exc
    assert dec.stdout is not None and enc.stdin is not None
    frame_bytes = out_width * height * 3
    index = 0
    expected = max(1.0, duration * 10)
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise ReframeError("cancelled", "Reframing cancelled.")
            chunk = dec.stdout.read(frame_bytes)
            if len(chunk) < frame_bytes:
                break
            frame = np.frombuffer(chunk, dtype=np.uint8).reshape(height, out_width, 3).copy()
            kf = _keyframe_at(index / 10.0)
            if kf is not None:
                x0 = int(round(kf.x * sx))
                w0 = max(2, int(round(kf.w * sx)))
                frame[:, max(0, x0) : min(out_width, x0 + w0), :] = (
                    frame[:, max(0, x0) : min(out_width, x0 + w0), :].astype(np.float32) * 0.55
                ).astype(np.uint8)
                frame[:, max(0, x0), :] = (0, 0, 255)
                frame[:, min(out_width - 1, x0 + w0 - 1), :] = (0, 0, 255)
            enc.stdin.write(frame.tobytes())
            index += 1
            if index % 30 == 0:
                frac = index / expected
                (progress or (lambda f, m: None))(min(frac, 0.99), "Rendering debug video")
    finally:
        for proc in (dec, enc):
            if proc.poll() is None:
                proc.terminate()
        try:
            enc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        dec.wait(timeout=30)
        enc.wait(timeout=60)
    if index == 0:
        raise ReframeError("no_video_stream", "Debug video produced no frames.")
    return out_path
