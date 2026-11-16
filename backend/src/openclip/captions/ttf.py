"""Minimal TrueType metric reader.

libass does not interpret ``Style: Fontsize`` as an em size: it scales glyphs so
that the font's ``usWinAscent + usWinDescent`` box equals ``Fontsize``. OpenClip
contracts define ``CaptionStyle.font_size`` as em pixels on the 1080x1920
canvas, so the ASS generator has to divide by that ratio.

Reading those two numbers straight from the TTF header keeps the conversion
exact and offline. Only the ``head`` and ``OS/2`` tables are needed.
"""

import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Fallback ratio when a font file cannot be read: 1.2 em covers most families.
DEFAULT_WINDOW_RATIO = 1.2


@dataclass(frozen=True)
class TtfMetrics:
    """The two header values libass uses to scale a font."""

    units_per_em: int
    win_ascent: int
    win_descent: int

    @property
    def window_ratio(self) -> float:
        """Fraction of ``Fontsize`` that one em occupies."""
        total = self.win_ascent + self.win_descent
        if self.units_per_em <= 0 or total <= 0:
            return DEFAULT_WINDOW_RATIO
        return total / self.units_per_em


def _table_offsets(data: bytes) -> dict[str, tuple[int, int]]:
    """Map table tags to their ``(offset, length)`` entries."""
    if len(data) < 12:
        raise ValueError("not a TrueType file")
    count = struct.unpack(">H", data[4:6])[0]
    tables: dict[str, tuple[int, int]] = {}
    for index in range(count):
        entry = 12 + index * 16
        if entry + 16 > len(data):
            break
        tag = data[entry : entry + 4].decode("latin-1")
        offset, length = struct.unpack(">II", data[entry + 8 : entry + 16])
        tables[tag] = (offset, length)
    return tables


@lru_cache(maxsize=32)
def read_ttf_metrics(path: Path) -> TtfMetrics | None:
    """Read ``unitsPerEm`` and the OS/2 window ascent/descent of one font file.

    Returns ``None`` for unreadable or unsupported files instead of raising:
    caption building must degrade gracefully to an estimate.
    """
    try:
        data = path.read_bytes()
        tables = _table_offsets(data)
        if "head" not in tables or "OS/2" not in tables:
            return None
        head_offset = tables["head"][0]
        units_per_em = struct.unpack(">H", data[head_offset + 18 : head_offset + 20])[0]
        os2_offset = tables["OS/2"][0]
        win_ascent, win_descent = struct.unpack(">HH", data[os2_offset + 74 : os2_offset + 78])
    except (OSError, ValueError, struct.error):
        return None
    if units_per_em <= 0 or win_ascent + win_descent <= 0:
        return None
    return TtfMetrics(units_per_em=units_per_em, win_ascent=win_ascent, win_descent=win_descent)
