"""Candidate window generation aligned on sentence boundaries."""

import logging
import math
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from openclip.analyze.features import build_context, window_features
from openclip.analyze.scoring import STOPWORDS, heuristic_score, longest_word_gap
from openclip.core.config import Settings
from openclip.core.errors import AnalysisError
from openclip.core.models import Candidate, JobOptions, MediaInfo, Transcript, Word

log = logging.getLogger(__name__)

_PAUSE_SECONDS = 0.4
_MAX_CANDIDATES = 250
_MAX_WINDOWS_PER_START = 3
_WORD_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_END = re.compile(r'[.!?…]["\'\)\]]*$')


@dataclass
class _Sentence:
    """A sentence-level unit with word timings."""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


def _split_sentences(transcript: Transcript) -> list[_Sentence]:
    """Split the transcript into sentence units on punctuation/segment ends."""
    sentences: list[_Sentence] = []
    for seg in transcript.segments:
        words = list(seg.words)
        if not words:
            if seg.text.strip():
                sentences.append(_Sentence(seg.start, seg.end, seg.text.strip(), []))
            continue
        current: list[Word] = []
        cur_start = words[0].start
        for word in words:
            current.append(word)
            if _SENTENCE_END.search(word.text.strip()):
                sentences.append(
                    _Sentence(
                        cur_start,
                        word.end,
                        " ".join(w.text for w in current).strip(),
                        list(current),
                    )
                )
                current = []
        if current:
            # Segment tail without terminal punctuation: treat the segment
            # boundary as the best available sentence end.
            sentences.append(
                _Sentence(
                    cur_start,
                    max(seg.end, current[-1].end),
                    " ".join(w.text for w in current).strip(),
                    list(current),
                )
            )
    return [s for s in sentences if s.text]


def _content_words(text: str) -> set[str]:
    """Lowercased content words with stopwords removed."""
    return {w for w in _WORD_RE.findall(text.lower()) if w not in STOPWORDS and len(w) > 2}


def _cosine(a: set[str], b: set[str]) -> float:
    """Cosine similarity between two bag-of-words sets."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / math.sqrt(len(a) * len(b))


def _topic_shift_scores(sentences: list[_Sentence]) -> list[float]:
    """Boundary score after each sentence: 1 - lexical similarity of neighbors.

    Compares the two sentences before and after each boundary; low similarity
    means a likely topic shift, which is a good cut point.
    """
    n = len(sentences)
    scores = [0.5] * n
    for i in range(1, n - 1):
        before = _content_words(sentences[i - 1].text) | _content_words(
            sentences[max(0, i - 2)].text
        )
        after = _content_words(sentences[i + 1].text) | _content_words(
            sentences[min(n - 1, i + 2)].text
        )
        scores[i] = 1.0 - _cosine(before, after)
    return scores


def _pause_before(sentences: list[_Sentence], index: int) -> float:
    """Silence between the previous sentence end and this sentence start."""
    if index <= 0:
        return 0.0
    prev = sentences[index - 1]
    cur = sentences[index]
    if not prev.words or not cur.words:
        return max(0.0, cur.start - prev.end)
    return max(0.0, cur.words[0].start - prev.words[-1].end)


def _pause_after(sentences: list[_Sentence], index: int) -> float:
    """Silence between this sentence end and the next sentence start."""
    if index >= len(sentences) - 1:
        return 0.0
    cur = sentences[index]
    nxt = sentences[index + 1]
    if not cur.words or not nxt.words:
        return max(0.0, nxt.start - cur.end)
    return max(0.0, nxt.words[0].start - cur.words[-1].end)


def _generate_windows(
    sentences: list[_Sentence], min_s: float, max_s: float
) -> list[tuple[int, int]]:
    """Slide over sentence indices emitting windows inside the duration band."""
    windows: list[tuple[int, int]] = []
    n = len(sentences)
    for i in range(n):
        valid_ends: list[int] = []
        for j in range(i, n):
            duration = sentences[j].end - sentences[i].start
            if duration > max_s:
                break
            if duration >= min_s:
                valid_ends.append(j)
        if not valid_ends:
            continue
        # Emit up to _MAX_WINDOWS_PER_START lengths spread across the band.
        picks = {valid_ends[0], valid_ends[-1]}
        if len(valid_ends) > 2:
            picks.add(valid_ends[len(valid_ends) // 2])
        for j in sorted(picks):
            windows.append((i, j))
    return windows


def _dedupe_windows(
    windows: list[tuple[int, int]], sentences: list[_Sentence], min_s: float
) -> list[tuple[int, int]]:
    """Drop windows that nearly duplicate an earlier one."""
    kept: list[tuple[int, int]] = []
    for win in windows:
        s, e = sentences[win[0]].start, sentences[win[1]].end
        duplicate = False
        for other in kept:
            os_, oe = sentences[other[0]].start, sentences[other[1]].end
            if abs(s - os_) < min_s * 0.5 and abs(e - oe) < min_s * 0.5:
                duplicate = True
                break
        if not duplicate:
            kept.append(win)
    return kept


def _hook_text(sentences: list[_Sentence], first: int, last: int) -> str:
    """Text of roughly the first 3 seconds of a window."""
    parts: list[str] = []
    deadline = sentences[first].start + 3.0
    for i in range(first, last + 1):
        s = sentences[i]
        if s.start > deadline and parts:
            break
        parts.append(s.text)
    return " ".join(parts)


def find_candidates(
    transcript: Transcript,
    media: MediaInfo,
    cfg: JobOptions,
    *,
    settings: Settings | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[Candidate]:
    """Generate sentence-aligned candidate windows with features and scores.

    Windows span ``min_clip_seconds``..``max_clip_seconds`` of speech, start
    and end on sentence boundaries, prefer pauses >= 0.4 s, and are deduplicated.
    A source shorter than the minimum yields a single flagged candidate.
    """
    del progress, cancel  # reserved for pipeline integration
    settings = settings or Settings()
    sentences = _split_sentences(transcript)
    if not sentences:
        raise AnalysisError("Transcript contains no sentences to analyze.")

    min_s = float(cfg.min_clip_seconds)
    max_s = float(cfg.max_clip_seconds)
    speech_start = sentences[0].start
    speech_end = sentences[-1].end

    if speech_end - speech_start < min_s:
        windows = [(0, len(sentences) - 1)]
    else:
        windows = _dedupe_windows(_generate_windows(sentences, min_s, max_s), sentences, min_s)

    ctx = build_context(transcript, media, settings)
    shift_scores = _topic_shift_scores(sentences)

    candidates: list[Candidate] = []
    for idx, (first, last) in enumerate(
        sorted(
            windows,
            key=lambda w: (sentences[w[0]].start, -(sentences[w[1]].end - sentences[w[0]].start)),
        )
    ):
        window_sents = sentences[first : last + 1]
        start = window_sents[0].start
        end = window_sents[-1].end
        words = [w for s in window_sents for w in s.words]
        texts = [s.text for s in window_sents]
        word_starts = [w.start for w in words]
        word_ends = [w.end for w in words]
        longest_gap = longest_word_gap(word_starts, word_ends)
        hook_text = _hook_text(sentences, first, last)
        features = window_features(
            ctx,
            texts,
            len(words),
            start,
            end,
            hook_text,
        )
        # Boundary quality: sentence alignment (always 1 here), pauses at the
        # edges, and the lexical topic shift just outside the window.
        pause_score = min(
            1.0,
            (_pause_before(sentences, first) + _pause_after(sentences, last))
            / (2 * _PAUSE_SECONDS),
        )
        shift = 0.5 * (shift_scores[first] if first < len(shift_scores) else 0.5)
        shift += 0.5 * (shift_scores[last] if last < len(shift_scores) else 0.5)
        boundary_quality = 0.5 + 0.3 * pause_score + 0.2 * shift
        score = heuristic_score(
            features,
            boundary_quality=boundary_quality,
            longest_gap=longest_gap,
            text=" ".join(texts),
            duration_seconds=end - start,
        )
        candidates.append(
            Candidate(
                id=f"candidate-{idx + 1:03d}",
                start=round(start, 3),
                end=round(end, 3),
                text=" ".join(texts),
                features=features,
                heuristic_score=score,
            )
        )
        if idx >= _MAX_CANDIDATES:
            break

    candidates.sort(key=lambda c: (-c.heuristic_score, c.start))
    candidates = candidates[:_MAX_CANDIDATES]
    # Re-number ids deterministically by score rank.
    for i, c in enumerate(candidates):
        c.id = f"candidate-{i + 1:03d}"
    return candidates
