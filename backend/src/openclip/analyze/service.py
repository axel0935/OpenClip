"""Highlight analysis orchestration: candidates -> clips -> selection."""

import logging
import re
import threading
from collections.abc import Callable

from openclip.analyze.candidates import find_candidates
from openclip.analyze.llm import (
    LLMClipJudgment,
    LLMProvider,
    build_provider,
    judge_candidates,
)
from openclip.analyze.scoring import STOPWORDS
from openclip.analyze.select import select_clips
from openclip.core.config import Settings
from openclip.core.errors import AnalysisError
from openclip.core.models import (
    Candidate,
    Clip,
    ClipSource,
    JobOptions,
    MediaInfo,
    Transcript,
)

log = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str], None]

_LLM_TOP_K = 12
_TITLE_MAX = 60
_WORD_RE = re.compile(r"[a-zA-Z']+")


def _no_op(_fraction: float, _message: str) -> None:
    """Default progress sink."""


def _report(progress: ProgressCallback | None, fraction: float, message: str) -> None:
    (progress or _no_op)(max(0.0, min(1.0, fraction)), message)


def _truncate(text: str, limit: int) -> str:
    """Truncate at a word boundary within ``limit`` characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") or text[:limit]


def _sentences_of(text: str) -> list[str]:
    """Split candidate text into rough sentences for metadata rules."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _hook_candidates(sentences: list[str]) -> str:
    """Pick the most attention-grabbing sentence for the hook line."""
    if not sentences:
        return ""

    def _rank(s: str) -> tuple[int, int]:
        score = 0
        if "?" in s:
            score += 2
        if re.search(r"\d", s):
            score += 1
        lowered = s.lower()
        for term in ("never", "secret", "truth", "why", "how", "stop", "mistake", "actually"):
            if term in lowered:
                score += 1
        return (-score, len(s))

    return min(sentences[:4], key=_rank)


def _hashtags(text: str, count: int = 4) -> list[str]:
    """Derive 3-5 hashtags from the most frequent content words."""
    freq: dict[str, int] = {}
    for word in _WORD_RE.findall(text.lower()):
        if len(word) > 3 and word not in STOPWORDS:
            freq[word] = freq.get(word, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    tags = [f"#{w}" for w, _ in ranked[:count]]
    while len(tags) < 3:
        tags.append("#openclip")
    return tags[:5]


def _title_source(sentences: list[str]) -> str:
    """Pick a substantive sentence for the clip title."""
    meaty = [s for s in sentences if len(s.split()) >= 5]
    if meaty:
        return meaty[0]
    if sentences:
        return max(sentences, key=lambda s: len(s.split()))
    return ""


def _heuristic_metadata(
    candidate: Candidate, min_clip_seconds: float
) -> tuple[str, str, list[str], str]:
    """Generate title/hook/hashtags/rationale with simple English rules."""
    sentences = _sentences_of(candidate.text)
    title_source = _title_source(sentences) or candidate.text
    title = _truncate(title_source, _TITLE_MAX)
    if title:
        title = title[0].upper() + title[1:]
    hook = _truncate(_hook_candidates(sentences) or title_source, 120)
    tags = _hashtags(candidate.text)
    parts = [
        f"Heuristic score {candidate.heuristic_score:.0f}.",
        f"{candidate.features.speech_rate_wps:.1f} words/s, "
        f"{candidate.features.question_count} question(s), "
        f"{candidate.features.scene_changes} scene change(s).",
    ]
    if candidate.end - candidate.start < min_clip_seconds:
        parts.append("Short source: clip covers the available speech.")
    return title, hook, tags, " ".join(parts)


def _clip_from_judgment(
    candidate: Candidate, judgment: LLMClipJudgment, min_clip_seconds: float
) -> Clip:
    """Build a Clip from validated LLM output with blended scoring."""
    score = 0.6 * judgment.score + 0.4 * candidate.heuristic_score
    return Clip(
        id=candidate.id.replace("candidate-", "clip-"),
        start=candidate.start,
        end=candidate.end,
        score=round(max(0.0, min(100.0, score)), 2),
        title=judgment.title,
        hook=judgment.hook,
        hashtags=judgment.hashtags,
        rationale=judgment.rationale,
        source=ClipSource.LLM,
    )


def _clip_heuristic(candidate: Candidate, min_clip_seconds: float) -> Clip:
    """Build a Clip using heuristic score and rule-based metadata."""
    title, hook, tags, rationale = _heuristic_metadata(candidate, min_clip_seconds)
    return Clip(
        id=candidate.id.replace("candidate-", "clip-"),
        start=candidate.start,
        end=candidate.end,
        score=candidate.heuristic_score,
        title=title or "Untitled moment",
        hook=hook or title or "Watch this",
        hashtags=tags,
        rationale=rationale,
        source=ClipSource.HEURISTIC,
    )


def score_candidates(
    candidates: list[Candidate],
    transcript: Transcript,
    cfg: JobOptions,
    *,
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[Clip]:
    """Score candidates into :class:`Clip` objects, optionally via the LLM judge.

    The judge sees only the top ``_LLM_TOP_K`` heuristic candidates. Mode
    ``auto`` falls back to heuristic scoring when the endpoint is unreachable;
    mode ``llm`` raises :class:`AnalysisError` instead.
    """
    if cancel is not None and cancel.is_set():
        raise AnalysisError("Analysis cancelled.")
    settings = settings or Settings()
    _report(progress, 0.6, "Scoring candidates")

    judgments: dict[str, LLMClipJudgment] | None = None
    mode = cfg.analysis_mode
    if mode != "heuristic":
        active = provider or build_provider(settings)
        if active is None:
            if mode == "llm":
                raise AnalysisError(
                    "analysis_mode 'llm' requires llm_base_url and llm_model settings."
                )
            log.info("No LLM endpoint configured; using heuristic scoring only.")
        elif not active.is_available():
            if mode == "llm":
                raise AnalysisError(
                    "LLM endpoint is unreachable; set analysis_mode to 'auto' or 'heuristic'."
                )
            log.info("LLM endpoint unreachable; using heuristic scoring only.")
        else:
            _report(progress, 0.7, "Judging top candidates with LLM")
            top = sorted(candidates, key=lambda c: -c.heuristic_score)[:_LLM_TOP_K]
            judgments = judge_candidates(active, top)
            if judgments is not None:
                _report(progress, 0.85, f"LLM judged {len(judgments)} candidates")

    clips: list[Clip] = []
    for candidate in candidates:
        judgment = judgments.get(candidate.id) if judgments else None
        if judgment is not None:
            clips.append(_clip_from_judgment(candidate, judgment, cfg.min_clip_seconds))
        else:
            clips.append(_clip_heuristic(candidate, cfg.min_clip_seconds))
    clips.sort(key=lambda c: (-c.score, c.start))
    return clips


def analyze(
    transcript: Transcript,
    media: MediaInfo,
    cfg: JobOptions,
    *,
    settings: Settings | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[Clip]:
    """Run the full highlight pipeline: candidates -> score -> select."""
    settings = settings or Settings()
    _report(progress, 0.05, "Finding candidate windows")
    candidates = find_candidates(
        transcript, media, cfg, settings=settings, progress=progress, cancel=cancel
    )
    _report(progress, 0.5, f"Found {len(candidates)} candidates")
    clips = score_candidates(
        candidates,
        transcript,
        cfg,
        settings=settings,
        progress=progress,
        cancel=cancel,
    )
    _report(progress, 0.9, "Selecting clips")
    selected = select_clips(clips, cfg)
    _report(progress, 1.0, f"Selected {len(selected)} clips")
    return selected
