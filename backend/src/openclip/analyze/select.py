"""Deterministic clip selection with overlap and gap constraints."""

from openclip.core.models import Clip, JobOptions

_MAX_CLIPS_DEFAULT = 8
_OVERLAP_RATIO_LIMIT = 0.2
_MIN_GAP_SECONDS = 5.0


def _duration(clip: Clip) -> float:
    return clip.end - clip.start


def _overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _separation(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Gap between two clips; 0 when they overlap or touch."""
    if a_end <= b_start:
        return b_start - a_end
    if b_end <= a_start:
        return a_start - b_end
    return 0.0


def _compatible(candidate: Clip, kept: list[Clip]) -> bool:
    """Check overlap (<20% of shorter) and minimum-gap rules."""
    for other in kept:
        overlap = _overlap_seconds(candidate.start, candidate.end, other.start, other.end)
        shorter = min(_duration(candidate), _duration(other))
        if shorter > 0 and overlap / shorter >= _OVERLAP_RATIO_LIMIT:
            return False
        # Slight overlaps below the ratio limit are tolerated; genuinely
        # separate clips must keep a 5 s gap when possible.
        if (
            overlap == 0.0
            and _separation(candidate.start, candidate.end, other.start, other.end)
            < _MIN_GAP_SECONDS
        ):
            return False
    return True


def select_clips(clips: list[Clip], cfg: JobOptions) -> list[Clip]:
    """Greedily select non-overlapping clips, best score first.

    A clip is kept only when its overlap with every already-kept clip is under
    20% of the shorter clip and separated clips keep a 5 s gap. Returns at
    most ``cfg.max_clips`` clips ordered by score descending.
    """
    ordered = sorted(clips, key=lambda c: (-c.score, c.start, c.end))
    kept: list[Clip] = []
    limit = cfg.max_clips or _MAX_CLIPS_DEFAULT
    for clip in ordered:
        if len(kept) >= limit:
            break
        if _compatible(clip, kept):
            kept.append(clip)
    return kept


def sort_chronologically(clips: list[Clip]) -> list[Clip]:
    """Return clips ordered by start time for timeline display."""
    return sorted(clips, key=lambda c: (c.start, c.end))
