"""Face-track association and main-subject selection with hysteresis."""

from dataclasses import dataclass, field

import numpy as np  # pyright: ignore[reportMissingImports] - vision extra

from openclip.reframe.detector import Face

_MATCH_DISTANCE_RATIO = 0.10  # of frame width
_DROPOUT_FRAMES = 3  # ~0.75 s at 4 fps
_SUBJECT_HOLD_SECONDS = 1.5
_SUBJECT_SWITCH_FACTOR = 1.5
_SCENE_DIFF_THRESHOLD = 18.0  # mean abs gray diff on 160 px-wide frames


@dataclass
class Track:
    """A face track across sampled frames (source-pixel coordinates)."""

    track_id: int
    cx: float
    cy: float
    area: float
    confidence: float
    hits: int = 1
    birth: int = 0
    last_seen: int = 0
    missing: int = 0

    def persistence(self, frame_index: int) -> float:
        """Fraction of frames since birth that produced a detection."""
        span = max(1, min(frame_index, self.last_seen) - self.birth + 1)
        return min(1.0, self.hits / span)

    def score(self, frame_index: int) -> float:
        """Subject score: area x confidence x persistence."""
        return self.area * max(0.05, self.confidence) * self.persistence(frame_index)


@dataclass
class FrameObservation:
    """Per-frame result: matched tracks and detections."""

    frame_index: int
    detections: list[Face]
    matched: dict[int, Face] = field(default_factory=dict)  # track_id -> face
    is_cut: float = 0.0  # scene-diff score, >0 marks a probable cut


def _centroid_distance(a_cx: float, a_cy: float, b: Face) -> float:
    return float(np.hypot(a_cx - b.cx, a_cy - b.cy))


def update_tracks(
    tracks: dict[int, Track],
    detections: list[Face],
    frame_index: int,
    max_distance: float,
    next_id: list[int],
) -> dict[int, Face]:
    """Greedy nearest-centroid matching; returns track_id -> face."""
    matched: dict[int, Face] = {}
    used_tracks: set[int] = set()
    used_faces: set[int] = set()
    # Sort detections by confidence so strong faces claim tracks first.
    order = sorted(range(len(detections)), key=lambda i: -detections[i].confidence)
    for di in order:
        face = detections[di]
        best_id: int | None = None
        best_dist = max_distance
        for tid, track in tracks.items():
            if tid in used_tracks or track.missing > _DROPOUT_FRAMES:
                continue
            dist = _centroid_distance(track.cx, track.cy, face)
            if dist < best_dist:
                best_dist = dist
                best_id = tid
        if best_id is None:
            tid = next_id[0]
            next_id[0] += 1
            tracks[tid] = Track(
                track_id=tid,
                cx=face.cx,
                cy=face.cy,
                area=face.area,
                confidence=face.confidence,
                birth=frame_index,
                last_seen=frame_index,
            )
            best_id = tid
        else:
            track = tracks[best_id]
            # Exponential smoothing of centroid/area keeps jitter low.
            alpha = 0.5
            track.cx = track.cx * (1 - alpha) + face.cx * alpha
            track.cy = track.cy * (1 - alpha) + face.cy * alpha
            track.area = track.area * (1 - alpha) + face.area * alpha
            track.confidence = 0.8 * track.confidence + 0.2 * face.confidence
            track.hits += 1
            track.last_seen = frame_index
            track.missing = 0
        matched[best_id] = face
        used_tracks.add(best_id)
        used_faces.add(di)
    for tid, track in tracks.items():
        if tid not in used_tracks:
            track.missing += 1
    # Drop long-dead tracks to bound memory.
    for tid in [t for t, tr in tracks.items() if tr.missing > _DROPOUT_FRAMES * 4]:
        del tracks[tid]
    return matched


class SubjectSelector:
    """Pick the main subject with hysteresis against brief challengers."""

    def __init__(self, fps: float = 4.0) -> None:
        self._fps = fps
        self._subject_id: int | None = None
        self._challenger_id: int | None = None
        self._challenger_since = 0
        self._subject_cx: float | None = None
        self._subject_missing = 0

    def update(
        self,
        tracks: dict[int, Track],
        matched: dict[int, Face],
        frame_index: int,
    ) -> float | None:
        """Return the subject centroid x for this frame, or ``None``."""
        live = [t for t in tracks.values() if t.missing <= _DROPOUT_FRAMES and t.hits >= 2]
        if not live:
            self._subject_missing += 1
            return self._subject_cx
        best = max(live, key=lambda t: t.score(frame_index))
        if self._subject_id is None or best.track_id == self._subject_id:
            self._challenger_id = None
            self._subject_id = best.track_id
            self._challenger_since = 0
        else:
            if self._challenger_id == best.track_id:
                held = (frame_index - self._challenger_since) / self._fps
            else:
                self._challenger_id = best.track_id
                self._challenger_since = frame_index
                held = 0.0
            # Require sustained dominance before switching subjects.
            if held >= _SUBJECT_HOLD_SECONDS and best.score(frame_index) > (
                tracks[self._subject_id].score(frame_index) * _SUBJECT_SWITCH_FACTOR
                if self._subject_id in tracks
                else 0.0
            ):
                self._subject_id = best.track_id
                self._challenger_id = None
                self._challenger_since = 0
        subject = tracks.get(self._subject_id) if self._subject_id is not None else None
        if subject is None:
            return None
        # Prefer the fresh detection centroid when available.
        face = matched.get(subject.track_id)
        self._subject_cx = face.cx if face is not None else subject.cx
        self._subject_missing = 0
        return self._subject_cx


def scene_diff(prev_gray: np.ndarray, cur_gray: np.ndarray) -> float:
    """Mean absolute difference between two grayscale frames."""
    return float(np.abs(prev_gray.astype(np.float32) - cur_gray.astype(np.float32)).mean())


def is_scene_cut(diff: float) -> bool:
    """Return True when a frame difference indicates a shot change."""
    return diff > _SCENE_DIFF_THRESHOLD
