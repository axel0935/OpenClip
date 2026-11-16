"""ASS timestamp helpers.

All caption timings are handled as integer centiseconds relative to the clip
start, which keeps rounding explicit and makes byte-identical output easy.
"""

CENTISECONDS_PER_SECOND = 100


def to_centiseconds(seconds: float) -> int:
    """Round a duration in seconds to non-negative integer centiseconds."""
    return max(0, int(round(seconds * CENTISECONDS_PER_SECOND)))


def ass_time(centiseconds: int) -> str:
    """Format integer centiseconds as an ASS timestamp ``H:MM:SS.cc``."""
    total = max(0, int(centiseconds))
    hours, remainder = divmod(total, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, hundredths = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{hundredths:02d}"


def ass_time_from_seconds(seconds: float) -> str:
    """Format a duration in seconds as an ASS timestamp."""
    return ass_time(to_centiseconds(seconds))
