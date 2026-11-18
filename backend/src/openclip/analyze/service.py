"""Highlight analysis orchestration: candidates -> clips -> selection."""

import logging
import math
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
from openclip.analyze.tags import is_banned_tag
from openclip.core.config import Settings
from openclip.core.errors import AnalysisError
from openclip.core.memory import available_memory_bytes
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

# The local judge needs roughly 2-3 GB on the reference machine (a 2B model plus
# its KV cache). Auto mode refuses to start it below this threshold so a low-RAM
# box degrades to heuristic scoring instead of thrashing swap for minutes.
LLM_MIN_AVAILABLE_BYTES = 3 * 1024 * 1024 * 1024

# Small English profanity list for display metadata only (titles, hooks,
# hashtags). Clip text and captions are never altered; masked words render as
# e.g. "f***" in titles/hooks and are dropped from hashtags. English only.
_PROFANITY = frozenset(
    """
    fuck fucking fucker fucked fucks shit shitty bullshit bitch bastard asshole
    ass damn hell crap piss dick pussy cunt whore slut hoe nigger nigga faggot
    retard wanker prick twat
    """.split()
)
_PROFANITY_RE = re.compile(
    r"\b(" + "|".join(sorted(_PROFANITY, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


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


def _mask_profanity(text: str) -> str:
    """Mask English profanity as ``f***`` for display metadata."""
    return _PROFANITY_RE.sub(lambda m: m.group(0)[0] + "***", text)


def _norm_text(text: str) -> str:
    """Lowercase, collapse whitespace for substring comparisons."""
    return " ".join(text.lower().split())


def _content_word_set(text: str) -> set[str]:
    """Content words (alpha, >3 chars, not stopwords) of a text."""
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 3 and w not in STOPWORDS}


def _title_supported(title: str, text: str) -> bool:
    """Cheap lexical check: the title must share words with the clip text.

    Titles with no checkable content words pass; otherwise at least half of
    the title's content words must appear in the candidate text.
    """
    title_words = _content_word_set(title)
    if not title_words:
        return True
    text_words = set(_WORD_RE.findall(text.lower()))
    hits = sum(1 for w in title_words if w in text_words)
    return hits >= max(1, math.ceil(len(title_words) * 0.5))


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


def _tag_words(text: str) -> list[str]:
    """Rank clean topic words of ``text`` by frequency, then alphabetically.

    A word qualifies when it is alphabetic, longer than three characters, not a
    stopword, not English profanity and not in the banned tag vocabulary
    (engagement bait, product and platform names) — so ``#openclip`` can never
    be produced from a transcript that merely mentions the tool.
    """
    freq: dict[str, int] = {}
    for word in _WORD_RE.findall(text.lower()):
        word = word.replace("'", "")
        if (
            len(word) > 3
            and word.isalpha()
            and word not in STOPWORDS
            and word not in _PROFANITY
            and not is_banned_tag(word)
        ):
            freq[word] = freq.get(word, 0) + 1
    return [word for word, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))]


def _hashtags(text: str, count: int = 4) -> list[str]:
    """Derive up to ``count`` clean topic hashtags from content words.

    Fewer than three tags are acceptable: padding with a product or filler tag
    would be worse than a short list, so the list is never filled artificially.
    """
    return [f"#{word}" for word in _tag_words(text)[:count]]


def _clean_hashtags(tags: list[str], fallback_text: str) -> list[str]:
    """Keep only clean lowercase topic tags; top up from ``fallback_text``.

    Never pads with a fixed tag: an empty or short list is returned as is.
    """
    cleaned: list[str] = []
    for tag in tags:
        word = tag.lstrip("#").lower()
        if (
            word
            and re.fullmatch(r"[a-z0-9_]+", word)
            and word not in _PROFANITY
            and not is_banned_tag(word)
            and f"#{word}" not in cleaned
        ):
            cleaned.append(f"#{word}")
    for tag in _hashtags(fallback_text):
        if len(cleaned) >= 3:
            break
        if tag not in cleaned:
            cleaned.append(tag)
    return cleaned[:5]


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
    title = _truncate(_mask_profanity(title_source), _TITLE_MAX)
    if title:
        title = title[0].upper() + title[1:]
    hook = _truncate(_mask_profanity(_hook_candidates(sentences) or title_source), 120)
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


def _clean_judgment(
    judgment: LLMClipJudgment, candidate: Candidate
) -> tuple[str, str, list[str], str, float]:
    """Repair LLM metadata against the output rules; fall back per field.

    - title: masked, <=60 chars, must share words with the clip text.
    - hook: masked and must be a verbatim line of the candidate text,
      otherwise replaced by the best heuristic sentence.
    - hashtags: profanity and filler tags dropped, topped up from the text.
    - score: clamped to 0-100.
    """
    title = _truncate(_mask_profanity(judgment.title.strip()), _TITLE_MAX)
    if not title or not _title_supported(title, candidate.text):
        sentences = _sentences_of(candidate.text)
        title = _truncate(_mask_profanity(_title_source(sentences) or candidate.text), _TITLE_MAX)
    if title:
        title = title[0].upper() + title[1:]

    hook = _truncate(_mask_profanity(judgment.hook.strip()), 160)
    text_norm = _norm_text(candidate.text)
    if not hook or _norm_text(hook) not in text_norm:
        sentences = _sentences_of(candidate.text)
        hook = _truncate(_mask_profanity(_hook_candidates(sentences) or title), 160)

    tags = _clean_hashtags(judgment.hashtags, candidate.text)
    score = max(0.0, min(100.0, judgment.score))
    rationale = _truncate(judgment.rationale.strip(), 200) or "LLM judgment."
    return title or "Untitled moment", hook or title or "Watch this", tags, rationale, score


def _clip_from_judgment(
    candidate: Candidate, judgment: LLMClipJudgment, min_clip_seconds: float
) -> Clip:
    """Build a Clip from validated LLM output with blended scoring."""
    title, hook, tags, rationale, llm_score = _clean_judgment(judgment, candidate)
    score = 0.6 * llm_score + 0.4 * candidate.heuristic_score
    return Clip(
        id=candidate.id.replace("candidate-", "clip-"),
        start=candidate.start,
        end=candidate.end,
        score=round(max(0.0, min(100.0, score)), 2),
        title=title,
        hook=hook,
        hashtags=tags,
        rationale=rationale,
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


def llm_skip_reason(cfg: JobOptions, available_bytes: int | None = None) -> str | None:
    """Return a short reason when auto mode must skip the LLM judge.

    Only ``analysis_mode=auto`` is guarded: an explicit ``llm`` request still
    starts the judge (the caller asked for it) and ``heuristic`` never uses it.
    ``None`` means "carry on normally".
    """
    if cfg.analysis_mode != "auto":
        return None
    free = available_memory_bytes() if available_bytes is None else available_bytes
    if free is None or free >= LLM_MIN_AVAILABLE_BYTES:
        return None
    return (
        f"Low memory: {free / 1024**3:.1f} GB available "
        f"(under the {LLM_MIN_AVAILABLE_BYTES / 1024**3:.0f} GB LLM guard), "
        "so the LLM judge was skipped and heuristic scores were used. "
        "Set analysis_mode=llm to force it."
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
    available_bytes: int | None = None,
) -> list[Clip]:
    """Score candidates into :class:`Clip` objects, optionally via the LLM judge.

    The judge sees only the top ``_LLM_TOP_K`` heuristic candidates. Mode
    ``auto`` falls back to heuristic scoring when the endpoint is unreachable
    and when the host is short on memory (below
    :data:`LLM_MIN_AVAILABLE_BYTES` of ``MemAvailable``); mode ``llm`` raises
    :class:`AnalysisError` instead of falling back.

    ``available_bytes`` overrides the ``/proc/meminfo`` probe (tests only);
    ``None`` means "probe the host".
    """
    if cancel is not None and cancel.is_set():
        raise AnalysisError("Analysis cancelled.")
    settings = settings or Settings()
    _report(progress, 0.6, "Scoring candidates")

    judgments: dict[str, LLMClipJudgment] | None = None
    mode = cfg.analysis_mode
    skip_reason = llm_skip_reason(cfg, available_bytes)
    if skip_reason is not None:
        log.info("Skipping LLM judge in auto mode: %s", skip_reason)
        _report(progress, 0.7, skip_reason)
    if mode != "heuristic" and skip_reason is None:
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

            def judge_progress(frac: float, msg: str) -> None:
                _report(progress, 0.7 + 0.15 * frac, msg)

            try:
                judgments = judge_candidates(
                    active,
                    top,
                    progress=judge_progress,
                    cancel=cancel,
                    batch_size=int(getattr(settings, "llm_batch_size", 5)),
                )
            finally:
                # Free the model RAM before the render stage (Ollama keep_alive 0).
                unload = getattr(active, "unload", None)
                if callable(unload):
                    unload()
            if judgments is not None:
                _report(progress, 0.85, f"LLM judged {len(judgments)} candidates")
            elif mode == "auto":
                _report(progress, 0.85, "LLM judge unavailable; heuristic scores used")

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
    available_bytes: int | None = None,
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
        available_bytes=available_bytes,
    )
    _report(progress, 0.9, "Selecting clips")
    selected = select_clips(clips, cfg)
    _report(progress, 1.0, f"Selected {len(selected)} clips")
    return selected
