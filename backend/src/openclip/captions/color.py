"""Colour conversion between OpenClip hex strings and ASS colour tags."""

from openclip.core.errors import ConfigError


def parse_color(value: str) -> tuple[int, int, int, int]:
    """Parse ``#RRGGBB`` or ``#RRGGBBAA`` into an ``(r, g, b, a)`` tuple.

    Alpha is given as opacity: ``FF`` is fully opaque, ``00`` fully transparent.
    """
    text = value.strip()
    if not text.startswith("#") or len(text) not in (7, 9):
        raise ConfigError(f"Invalid colour {value!r}: expected #RRGGBB or #RRGGBBAA.")
    try:
        channels = bytes.fromhex(text[1:])
    except ValueError as exc:
        raise ConfigError(f"Invalid colour {value!r}: expected hexadecimal digits.") from exc
    red, green, blue = channels[0], channels[1], channels[2]
    alpha = channels[3] if len(channels) == 4 else 255
    return red, green, blue, alpha


def ass_bgr(value: str) -> str:
    """Return the ``&HBBGGRR&`` form used by ``\\c`` overrides."""
    red, green, blue, _ = parse_color(value)
    return f"&H{blue:02X}{green:02X}{red:02X}&"


def ass_alpha(value: str) -> str:
    """Return the ``&HAA&`` alpha override; ASS alpha is inverted (00 is opaque)."""
    _, _, _, alpha = parse_color(value)
    return f"&H{255 - alpha:02X}&"


def ass_color(value: str) -> str:
    """Return the ``&HAABBGGRR`` form used in style lines and drawing tags."""
    red, green, blue, alpha = parse_color(value)
    return f"&H{255 - alpha:02X}{blue:02X}{green:02X}{red:02X}"


def color_tags(value: str, index: int = 1) -> str:
    """Return ASS overrides that set one colour channel (primary by default)."""
    return f"\\{index}c{ass_bgr(value)}\\{index}a{ass_alpha(value)}"
