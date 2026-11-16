"""Text measurement and line layout for caption rendering.

Two different pixel sizes matter here:

``font_size`` (contract)
    Em pixels on the 1080x1920 canvas. Pillow measures with exactly this size.
``Fontsize`` (ASS style)
    What libass receives. libass scales glyphs so that the font's
    ``usWinAscent + usWinDescent`` box equals ``Fontsize``, so the emitted value
    is ``font_size * window_ratio``. Without that correction text renders
    18-45 % too small, and word plates land in the wrong place.

When a font file is missing, measurement falls back to a conservative character
estimate and logs one warning per family.
"""

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from openclip.captions.fonts import font_path
from openclip.captions.ttf import DEFAULT_WINDOW_RATIO, read_ttf_metrics
from openclip.core.models import CaptionStyle

LOGGER = logging.getLogger(__name__)

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
#: Widest allowed caption block, leaving ~80 px of margin on each side.
MAX_TEXT_WIDTH = 920
#: A single unbreakable word wider than this is rejected outright.
HARD_MAX_WIDTH = 1040
#: Average glyph advance as a fraction of the em size, for the estimate fallback.
ESTIMATED_GLYPH_RATIO = 0.55
#: Multiplier that turns the em size into a fallback line advance when the
#: font's window ratio cannot be read.
ESTIMATED_LINE_RATIO = 1.35

_WARNED_FAMILIES: set[str] = set()


@dataclass(frozen=True)
class FontMetrics:
    """Resolved sizing information for one style."""

    path: str | None
    em_size: int
    window_ratio: float
    ass_fontsize: float
    line_advance: float

    @property
    def available(self) -> bool:
        """Whether a real font file backs these metrics."""
        return self.path is not None


@dataclass(frozen=True)
class WordBox:
    """Pixel rectangle of one word on the 1080x1920 canvas."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        """Rectangle width in pixels."""
        return self.right - self.left

    @property
    def height(self) -> float:
        """Rectangle height in pixels."""
        return self.bottom - self.top


@dataclass(frozen=True)
class LineLayout:
    """One laid-out caption line with its per-word boxes."""

    text: str
    width: float
    top: float
    word_boxes: tuple[WordBox, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BlockLayout:
    """A whole caption block (one or more lines), centred horizontally."""

    lines: tuple[LineLayout, ...]
    width: float
    height: float
    line_height: float

    @property
    def text(self) -> str:
        """The block text with ASS hard line breaks between lines."""
        return "\\N".join(line.text for line in self.lines)


@lru_cache(maxsize=64)
def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | None:
    """Load and cache a Pillow font for one file and pixel size."""
    try:
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
    except OSError:
        return None


@lru_cache(maxsize=32)
def _metrics_for(path: str | None, em_size: int, family: str) -> FontMetrics:
    """Compute sizing information for one font file and em size."""
    if path is None:
        if family not in _WARNED_FAMILIES:
            _WARNED_FAMILIES.add(family)
            LOGGER.warning(
                "Font %r is not bundled in assets/fonts; caption widths are estimated.",
                family,
            )
        return FontMetrics(
            path=None,
            em_size=em_size,
            window_ratio=DEFAULT_WINDOW_RATIO,
            ass_fontsize=em_size * DEFAULT_WINDOW_RATIO,
            line_advance=em_size * ESTIMATED_LINE_RATIO,
        )
    metrics = read_ttf_metrics(Path(path))
    ratio = metrics.window_ratio if metrics is not None else DEFAULT_WINDOW_RATIO
    # libass advances lines by exactly `Fontsize`, i.e. em * window_ratio, so
    # this is the only value that keeps multi-line blocks spaced correctly.
    ass_size = em_size * ratio
    return FontMetrics(
        path=path,
        em_size=em_size,
        window_ratio=ratio,
        ass_fontsize=ass_size,
        line_advance=ass_size,
    )


def font_metrics(style: CaptionStyle) -> FontMetrics:
    """Resolve metrics for a style, honouring its family, weight and size."""
    file_path = font_path(style.font_family, style.bold)
    resolved = str(file_path) if file_path is not None else None
    return _metrics_for(resolved, style.font_size, style.font_family)


def ass_fontsize(style: CaptionStyle) -> float:
    """Return the ``Fontsize`` value to write in the ASS style line."""
    return font_metrics(style).ass_fontsize


def line_height(style: CaptionStyle) -> float:
    """Return the line advance in canvas pixels."""
    return font_metrics(style).line_advance


def render_text(text: str, style: CaptionStyle) -> str:
    """Apply the style's case transformation."""
    return text.upper() if style.uppercase else text


def text_width(text: str, style: CaptionStyle) -> float:
    """Measure ``text`` in canvas pixels, including letter spacing.

    The text is case-folded first: ``uppercase`` styles render capitals, and
    capitals are wider than lower-case glyphs in most families, so measuring the
    raw string would under-estimate every line.

    libass applies ``Spacing`` between shaped glyph pairs; for ``n`` characters
    that adds ``n`` spacings. Pillow has no such option, so the same convention
    is applied here to keep both sides in agreement.
    """
    if not text:
        return 0.0
    measured = render_text(text, style)
    metrics = font_metrics(style)
    spacing = style.letter_spacing * len(measured)
    if metrics.path is None:
        return style.font_size * ESTIMATED_GLYPH_RATIO * len(measured) + spacing
    font = _load_font(metrics.path, metrics.em_size)
    if font is None:
        return style.font_size * ESTIMATED_GLYPH_RATIO * len(measured) + spacing
    return float(font.getlength(measured)) + spacing


def wrap_words(
    words: list[str], style: CaptionStyle, max_width: float = MAX_TEXT_WIDTH
) -> list[list[str]] | None:
    """Wrap ``words`` into at most ``style.max_lines`` balanced lines.

    Greedy wrapping first, then a rebalance pass: a final line holding a single
    word ("...watch them / and") is a classic short-form caption defect, so words
    move down from the longest line while both lines still fit.

    Returns ``None`` when the words do not fit. A single word wider than
    ``max_width`` is accepted on its own line while it stays under
    :data:`HARD_MAX_WIDTH`, since it cannot be broken further.
    """
    if not words:
        return None
    lines: list[list[str]] = [[]]
    for word in words:
        rendered = render_text(word, style)
        if lines[-1]:
            candidate = " ".join([*lines[-1], rendered])
            if text_width(candidate, style) <= max_width:
                lines[-1].append(rendered)
                continue
            lines.append([rendered])
        else:
            lines[-1].append(rendered)
        if text_width(" ".join(lines[-1]), style) > HARD_MAX_WIDTH:
            return None
    if len(lines) > style.max_lines:
        return None
    return _balance(lines, style, max_width)


def _balance(lines: list[list[str]], style: CaptionStyle, max_width: float) -> list[list[str]]:
    """Even out line lengths so the last line is never a lone word.

    Only moves words *down* the block, and only while every line stays within
    ``max_width``, so the result can never overflow.
    """
    if len(lines) < 2 or len(lines[-1]) > 1:
        return lines
    balanced = [list(line) for line in lines]
    for index in range(len(balanced) - 1, 0, -1):
        current, previous = balanced[index], balanced[index - 1]
        while len(current) < 2 and len(previous) > 2:
            moved = [previous[-1], *current]
            if text_width(" ".join(moved), style) > max_width:
                break
            balanced[index] = moved
            balanced[index - 1] = previous[:-1]
            current, previous = balanced[index], balanced[index - 1]
    return balanced


def layout_block(words: list[str], style: CaptionStyle) -> BlockLayout:
    """Lay words out into centred lines and compute their pixel boxes.

    Each line is horizontally centred on the canvas. Line ``i`` occupies the
    band ``[i * line_height, (i + 1) * line_height)`` measured from the top of
    the block, and word boxes describe the ink-free advance box of each word so
    that plates can be drawn behind them.
    """
    lines = wrap_words(words, style)
    if lines is None:
        lines = [[render_text(word, style) for word in words]]
    advance = line_height(style)
    widths = [text_width(" ".join(line), style) for line in lines]
    space = text_width(" ", style)
    line_layouts: list[LineLayout] = []
    for index, (line_words, width) in enumerate(zip(lines, widths, strict=True)):
        left = (CANVAS_WIDTH - width) / 2.0
        top = index * advance
        boxes: list[WordBox] = []
        cursor = left
        for word in line_words:
            word_width = text_width(word, style)
            boxes.append(WordBox(cursor, top, cursor + word_width, top + advance))
            cursor += word_width + space
        line_layouts.append(
            LineLayout(text=" ".join(line_words), width=width, top=top, word_boxes=tuple(boxes))
        )
    return BlockLayout(
        lines=tuple(line_layouts),
        width=max(widths) if widths else 0.0,
        height=advance * len(lines),
        line_height=advance,
    )
