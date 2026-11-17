"""Font discovery and the FFmpeg ``subtitles`` filter string.

Fonts live flat in ``assets/fonts`` during development and in
``openclip/data/fonts`` inside an installed wheel: one ``<Family>-<Weight>.ttf``
file per weight. Licence texts are kept next to that directory, never inside
it — see ``assets/licenses``.

The flat layout is required, not merely convenient: libass does **not** recurse
into sub-directories of ``fontsdir``. It logs a directory name as if it were a
font file, fails to load it, and silently falls back to a system font — which
makes every glyph measurement wrong. Keeping one level keeps libass and Pillow
in agreement.

Resolution order: ``OPENCLIP_FONTS_DIR``, then the installed package data, then
the repository copy. A deployment can point ``OPENCLIP_FONTS_DIR`` at another
flat directory.
"""

import logging
from pathlib import Path

from openclip.core.errors import ConfigError
from openclip.core.resources import env_dir, packaged_resource_dir, repo_dir

LOGGER = logging.getLogger(__name__)

FONTS_DIR_ENV = "OPENCLIP_FONTS_DIR"
_FONTS_RELATIVE = "fonts"
_WEIGHT_ORDER = ("Bold", "SemiBold", "Black", "ExtraBold", "Medium", "Regular")


def fonts_dir() -> Path:
    """Return the directory that holds the bundled caption fonts.

    ``OPENCLIP_FONTS_DIR`` wins over the packaged copy, which wins over the
    repository copy. :class:`ConfigError` is raised when nothing resolves.
    """
    override = env_dir(FONTS_DIR_ENV)
    if override is not None:
        return override
    for candidate in (packaged_resource_dir(_FONTS_RELATIVE), repo_dir("assets/fonts")):
        if candidate is not None:
            return candidate
    raise ConfigError(
        "Font directory not found. Set OPENCLIP_FONTS_DIR to a flat directory of TTF files."
    )


def fonts_dir_or_none() -> Path | None:
    """Return the fonts directory, or ``None`` when it cannot be resolved."""
    try:
        return fonts_dir()
    except ConfigError:
        return None


def _normalise(name: str) -> str:
    """Lower-case a name and drop separators, for loose matching."""
    return "".join(character for character in name.lower() if character.isalnum())


def _family_stem(stem: str) -> str:
    """Strip a trailing weight token from a font file stem."""
    parts = stem.rsplit("-", 1)
    if len(parts) == 2 and parts[1] in _WEIGHT_ORDER:
        return parts[0]
    return stem


#: File stems whose real family name has a space, which ASS matches by name.
_FAMILY_DISPLAY = {
    "archivoblack": "Archivo Black",
    "bebasneue": "Bebas Neue",
}


def family_display_name(stem: str) -> str:
    """Return the human-facing family name for a font file stem."""
    return _FAMILY_DISPLAY.get(stem.lower(), stem)


def font_path(family: str, bold: bool = False) -> Path | None:
    """Return the TTF file for ``family`` at the requested weight.

    ``None`` means the family is not bundled; callers fall back to estimated
    metrics and libass falls back to its own default font.
    """
    root = fonts_dir_or_none()
    if root is None:
        return None
    wanted = _normalise(family)
    candidates = [
        path for path in sorted(root.glob("*.ttf")) if _normalise(_family_stem(path.stem)) == wanted
    ]
    if not candidates:
        return None
    for hint in ("Bold", "SemiBold") if bold else ("Regular", "Medium"):
        for path in candidates:
            if path.stem.endswith(hint):
                return path
    return candidates[0]


def available_families() -> list[str]:
    """List the bundled font family names, sorted alphabetically."""
    root = fonts_dir_or_none()
    if root is None:
        return []
    return sorted({family_display_name(_family_stem(path.stem)) for path in root.glob("*.ttf")})


def _quote_filter_value(value: str) -> str:
    """Quote one filter option value so FFmpeg's filtergraph parser keeps it intact."""
    text = str(value).replace("\\", "/")
    for character in ("'", ":", ",", ";", "[", "]"):
        text = text.replace(character, f"\\{character}")
    return f"'{text}'"


def ffmpeg_subtitles_filter(ass_path: Path | str, fonts_dir: Path | str | None = None) -> str:
    """Build ``subtitles=filename=...:fontsdir=...`` for the FFmpeg ``subtitles`` filter.

    Both paths are quoted and escaped so spaces, colons, commas and apostrophes
    survive the filtergraph parser. The result is passed as a single ``-vf``
    argument; no shell is ever involved.
    """
    subtitles = _quote_filter_value(str(ass_path))
    directory = Path(fonts_dir) if fonts_dir is not None else fonts_dir_or_none()
    if directory is None:
        LOGGER.warning("No fonts directory available; libass will use system fonts.")
        return f"subtitles=filename={subtitles}"
    return f"subtitles=filename={subtitles}:fontsdir={_quote_filter_value(str(directory))}"
