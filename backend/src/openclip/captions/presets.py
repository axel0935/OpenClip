"""Loading of the JSON caption presets into validated :class:`CaptionStyle` models."""

import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from openclip.core.errors import ConfigError
from openclip.core.models import CaptionStyle

LOGGER = logging.getLogger(__name__)

PRESETS_DIR_ENV = "OPENCLIP_PRESETS_DIR"
_REPO_MARKERS = ("presets/captions", "assets/fonts")


def _repo_presets_dir() -> Path | None:
    """Find ``presets/captions`` by walking up from this module."""
    current = Path(__file__).resolve().parent
    for candidate in [current, *current.parents]:
        if all((candidate / marker).exists() for marker in _REPO_MARKERS):
            return candidate / "presets" / "captions"
    return None


def presets_dir(directory: Path | str | None = None) -> Path:
    """Resolve the preset directory: argument, ``OPENCLIP_PRESETS_DIR``, then the repo copy."""
    if directory is not None:
        candidate = Path(directory).expanduser()
        if not candidate.is_dir():
            raise ConfigError(f"Preset directory {candidate} does not exist.")
        return candidate
    override = os.environ.get(PRESETS_DIR_ENV)
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_dir():
            raise ConfigError(f"{PRESETS_DIR_ENV}={override!r} is not a directory.")
        return candidate
    repo_dir = _repo_presets_dir()
    if repo_dir is None or not repo_dir.is_dir():
        raise ConfigError(
            "Caption presets not found. Set OPENCLIP_PRESETS_DIR to a directory of JSON presets."
        )
    return repo_dir


def load_style(path: Path) -> CaptionStyle:
    """Load and validate one preset file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read caption preset {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Caption preset {path.name} is not valid JSON: {exc}") from exc
    try:
        return CaptionStyle.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"Caption preset {path.name} is invalid: {_format_validation(exc)}"
        ) from exc


def _format_validation(exc: ValidationError) -> str:
    """Render Pydantic errors as one short, field-oriented sentence."""
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def load_presets(directory: Path | str | None = None) -> dict[str, CaptionStyle]:
    """Load every ``*.json`` preset in ``directory`` keyed by style id, sorted by id."""
    root = presets_dir(directory)
    styles: dict[str, CaptionStyle] = {}
    files = sorted(root.glob("*.json"))
    if not files:
        raise ConfigError(f"No caption presets found in {root}.")
    for path in files:
        style = load_style(path)
        if style.id in styles:
            raise ConfigError(f"Duplicate caption preset id {style.id!r} in {path.name}.")
        if path.stem != style.id:
            LOGGER.warning("Preset file %s declares id %r.", path.name, style.id)
        styles[style.id] = style
    return dict(sorted(styles.items()))
