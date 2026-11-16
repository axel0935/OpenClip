"""Grouping of clip words into caption chunks with stable timings."""

import re
from dataclasses import dataclass

from openclip.captions.text import BlockLayout, WordBox, layout_block, render_text, wrap_words
from openclip.core.models import CaptionStyle, Clip, Transcript

#: Extra on-screen time after the last word of a chunk, in seconds.
TAIL_PADDING = 0.15
#: Shortest on-screen time for a chunk, in seconds.
MIN_DISPLAY = 0.3
#: A silence longer than this starts a new chunk, in seconds.
PAUSE_SPLIT = 0.5
#: Maximum number of words in one chunk.
MAX_WORDS = 6

_SENTENCE_END = re.compile(r"[.!?\u2026][\"'\u201d\u2019)\]]*$")
_CLAUSE_END = re.compile(r"[,;:][\"'\u201d\u2019)\]]*$")


@dataclass(frozen=True)
class ChunkWord:
    """One word with clip-relative timings."""

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class CaptionChunk:
    """A group of words displayed together, with its measured layout."""

    words: tuple[ChunkWord, ...]
    start: float
    end: float
    block: BlockLayout

    @property
    def word_boxes(self) -> tuple[WordBox, ...]:
        """Pixel boxes for every word, in reading order."""
        return tuple(box for line in self.block.lines for box in line.word_boxes)


def clip_words(transcript: Transcript, clip: Clip) -> list[ChunkWord]:
    """Collect words that overlap ``clip``, clamped and shifted to clip time.

    Words entirely outside the clip are dropped; words straddling an edge are
    clamped to it. Segments are flattened in time order and exact duplicates
    produced by overlapping segments are dropped.
    """
    duration = max(0.0, clip.end - clip.start)
    collected: list[tuple[float, float, str]] = []
    for segment in transcript.segments:
        for word in segment.words:
            if word.end <= clip.start or word.start >= clip.end:
                continue
            text = word.text.strip()
            if not text:
                continue
            start = min(max(word.start - clip.start, 0.0), duration)
            end = min(max(word.end - clip.start, start), duration)
            collected.append((start, end, text))
    collected.sort(key=lambda item: (item[0], item[1]))
    unique: list[tuple[float, float, str]] = []
    for item in collected:
        if unique and abs(unique[-1][0] - item[0]) < 1e-6 and unique[-1][2] == item[2]:
            continue
        unique.append(item)
    return [ChunkWord(text=text, start=start, end=end) for start, end, text in unique]


def _ends_sentence(text: str) -> bool:
    """Whether a word closes a sentence."""
    return bool(_SENTENCE_END.search(text))


def _ends_clause(text: str) -> bool:
    """Whether a word closes a clause, a softer break than a sentence end."""
    return bool(_CLAUSE_END.search(text))


def _group(words: list[ChunkWord], style: CaptionStyle) -> list[list[ChunkWord]]:
    """Split words into chunks by word budget, punctuation and pauses."""
    limit = max(1, min(style.words_per_caption, MAX_WORDS))
    clause_at = max(2, limit - 1)
    chunks: list[list[ChunkWord]] = []
    current: list[ChunkWord] = []
    for index, word in enumerate(words):
        current.append(word)
        following = words[index + 1] if index + 1 < len(words) else None
        if following is None:
            chunks.append(current)
            break
        gap = following.start - word.end
        if len(current) >= limit:
            chunks.append(current)
            current = []
        elif _ends_sentence(word.text):
            chunks.append(current)
            current = []
        elif gap > PAUSE_SPLIT:
            chunks.append(current)
            current = []
        elif _ends_clause(word.text) and len(current) >= clause_at:
            chunks.append(current)
            current = []
    return chunks


def _raw_words(chunk: list[ChunkWord]) -> list[str]:
    """Word texts as fed into layout, before case transformation."""
    return [word.text for word in chunk]


def _fits(chunk: list[ChunkWord], style: CaptionStyle) -> bool:
    """Whether a chunk wraps into the style's line and width budget."""
    return wrap_words(_raw_words(chunk), style) is not None


def _split_to_fit(chunk: list[ChunkWord], style: CaptionStyle) -> list[list[ChunkWord]]:
    """Recursively halve a chunk until every piece fits the width budget."""
    if len(chunk) <= 1:
        return [chunk]
    if _fits(chunk, style):
        return [chunk]
    middle = len(chunk) // 2
    return [*_split_to_fit(chunk[:middle], style), *_split_to_fit(chunk[middle:], style)]


def _fix_orphan(chunks: list[list[ChunkWord]], style: CaptionStyle) -> list[list[ChunkWord]]:
    """Avoid a one-word final chunk by moving one word out of the previous chunk.

    A single word left alone at the end of a sentence reads badly on screen, but
    merging must never cross a sentence boundary: ``"...friend."`` followed by
    ``"How"`` is two captions, not one.
    """
    if len(chunks) < 2:
        return chunks
    last, previous = chunks[-1], chunks[-2]
    if len(last) != 1 or len(previous) < 2:
        return chunks
    if _ends_sentence(previous[-1].text):
        return chunks
    merged = [*previous[-1:], *last]
    if not _fits(merged, style):
        return chunks
    return [*chunks[:-2], previous[:-1], merged]


def build_chunks(
    words: list[ChunkWord], style: CaptionStyle, clip_duration: float
) -> list[CaptionChunk]:
    """Group clip words into timed, measured caption chunks.

    Chunks never overlap. A chunk ends :data:`TAIL_PADDING` after its last word
    but no later than the next chunk starts, and is shown for at least
    :data:`MIN_DISPLAY` seconds whenever the gap allows it.
    """
    if not words or clip_duration <= 0:
        return []
    groups: list[list[ChunkWord]] = []
    for group in _group(words, style):
        groups.extend(_split_to_fit(group, style))
    groups = _fix_orphan(groups, style)

    chunks: list[CaptionChunk] = []
    for index, group in enumerate(groups):
        start = max(0.0, group[0].start)
        next_start = (
            max(0.0, groups[index + 1][0].start) if index + 1 < len(groups) else clip_duration
        )
        end = max(start + MIN_DISPLAY, group[-1].end + TAIL_PADDING)
        end = min(end, next_start, clip_duration)
        if end <= start:
            continue
        block = layout_block(_raw_words(group), style)
        chunks.append(CaptionChunk(words=tuple(group), start=start, end=end, block=block))
    return chunks


def escape_ass_text(text: str) -> str:
    """Escape characters that ASS would otherwise treat as override syntax."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def case_text(text: str, style: CaptionStyle) -> str:
    """Apply the style's case rule and escape the result for ASS."""
    return escape_ass_text(render_text(text, style))


__all__ = [
    "CaptionChunk",
    "ChunkWord",
    "build_chunks",
    "case_text",
    "clip_words",
    "escape_ass_text",
]
