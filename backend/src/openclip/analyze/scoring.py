"""Heuristic highlight scoring with named, documented weights."""

import re
from dataclasses import dataclass

from openclip.core.models import CandidateFeatures

# Conservative filler set: only words that carry no propositional content.
# Common words like "just", "really" or "you" are intentionally excluded so
# normal speech is not penalized.
_FILLER_WORDS = {
    "um",
    "uh",
    "erm",
    "hmm",
    "like",
    "basically",
    "actually",
    "kinda",
    "sorta",
    "gonna",
    "wanna",
}

_WORD_RE = re.compile(r"[a-zA-Z']+")

# Shared English stopword list used by candidate and metadata code.
STOPWORDS = frozenset(
    """
    the a an and or but if then so to of in on for with at by is are was were
    be been it its this that these those you your i we they he she not no do
    does did have has had will would can could should what when where who why
    how all just about into from up out there here my me him her them us
    """.split()
)


@dataclass(frozen=True)
class HeuristicWeights:
    """Named weights of the 0-100 heuristic highlight score.

    Every component is documented in ``docs/ARCHITECTURE.md`` under
    "Highlight scoring"; keep both in sync when tuning.
    """

    hook: float = 30.0
    """Opening-grab score from :func:`features.hook_score` (0-1)."""
    speech_rate: float = 15.0
    """Closeness of words-per-second to the ~3 wps sweet spot."""
    audio_energy: float = 10.0
    """Percentile-normalized loudness of the window (0-1)."""
    question: float = 5.0
    """Bonus scaled by the number of questions in the window."""
    scene_change: float = 5.0
    """Bonus scaled by the density of detected shot changes."""
    boundary: float = 10.0
    """Sentence alignment plus pause/topic-shift boundary quality (0-1)."""
    duration: float = 8.0
    """Length preference: bonus for 30-60 s sentence-aligned windows,
    penalty above ~75 s, nothing at the minimum bound."""
    silence_penalty: float = 10.0
    """Penalty for dead air: longest internal word gap beyond ~1.5 s."""
    filler_penalty: float = 10.0
    """Penalty for dense filler words such as 'um', 'like', 'you know'."""


DEFAULT_WEIGHTS = HeuristicWeights()

_IDEAL_WPS = 3.0
_WPS_TOLERANCE = 3.0


def speech_rate_score(wps: float) -> float:
    """Triangular score peaking at :data:`_IDEAL_WPS` words per second."""
    return max(0.0, 1.0 - abs(wps - _IDEAL_WPS) / _WPS_TOLERANCE)


def filler_density(text: str) -> float:
    """Fraction of words that are fillers."""
    words = _WORD_RE.findall(text.lower())
    if not words:
        return 0.0
    fillers = sum(1 for w in words if w in _FILLER_WORDS)
    return fillers / len(words)


def longest_word_gap(word_starts: list[float], word_ends: list[float]) -> float:
    """Longest silence between consecutive words in a window."""
    if len(word_starts) < 2:
        return 0.0
    gaps = [s - e for s, e in zip(word_starts[1:], word_ends[:-1], strict=True)]
    return max(gaps) if gaps else 0.0


def duration_score(seconds: float) -> float:
    """Length preference in ``[-1, 1]`` for a candidate window.

    - ``< 30 s``: 0 — no bonus for sitting at the minimum clip length.
    - ``30-60 s``: ramps to 1.0 (reached at ~45 s) — the sweet spot for
      short-form clips, provided boundaries stay sentence-aligned (they do
      by construction).
    - ``60-75 s``: decays gently to 0.5.
    - ``> 75 s``: turns negative — a mild penalty for overlong windows.
    """
    if seconds < 30.0:
        return 0.0
    if seconds <= 60.0:
        return min(1.0, (seconds - 30.0) / 15.0)
    if seconds <= 75.0:
        return 1.0 - (seconds - 60.0) / 15.0 * 0.5
    return max(-1.0, 0.5 - (seconds - 75.0) / 25.0)


def heuristic_score(
    features: CandidateFeatures,
    *,
    boundary_quality: float,
    longest_gap: float,
    text: str,
    duration_seconds: float = 0.0,
    weights: HeuristicWeights = DEFAULT_WEIGHTS,
) -> float:
    """Compute the 0-100 heuristic score for a candidate window.

    ``boundary_quality`` (0-1) combines sentence alignment, pause length at
    the edges and the lexical topic shift across the boundary.
    """
    components = {
        "hook": weights.hook * features.hook_score,
        "speech_rate": weights.speech_rate * speech_rate_score(features.speech_rate_wps),
        "audio_energy": weights.audio_energy * features.audio_energy,
        "question": weights.question * min(1.0, features.question_count / 2.0),
        "scene_change": weights.scene_change * min(1.0, features.scene_changes / 3.0),
        "boundary": weights.boundary * max(0.0, min(1.0, boundary_quality)),
        "duration": weights.duration * duration_score(duration_seconds),
    }
    silence = max(0.0, longest_gap - 1.5) / 3.0
    penalties = {
        "silence": weights.silence_penalty * min(1.0, silence),
        "filler": weights.filler_penalty * min(1.0, filler_density(text) / 0.15),
    }
    total = sum(components.values()) - sum(penalties.values())
    return round(max(0.0, min(100.0, total)), 2)
