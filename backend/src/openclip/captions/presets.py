"""Loading of the JSON caption presets into validated :class:`CaptionStyle` models."""

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from openclip.core.errors import ConfigError
from openclip.core.models import CaptionStyle
from openclip.core.resources import env_dir, packaged_resource_dir, repo_dir

LOGGER = logging.getLogger(__name__)

PRESETS_DIR_ENV = "OPENCLIP_PRESETS_DIR"
_PRESETS_RELATIVE = "presets/captions"


def presets_dir(directory: Path | str | None = None) -> Path:
    """Resolve the preset directory.

    Order: explicit argument, ``OPENCLIP_PRESETS_DIR``, installed package data,
    then the repository copy.
    """
    if directory is not None:
        candidate = Path(directory).expanduser()
        if not candidate.is_dir():
            raise ConfigError(f"Preset directory {candidate} does not exist.")
        return candidate
    override = env_dir(PRESETS_DIR_ENV)
    if override is not None:
        return override
    for candidate in (packaged_resource_dir(_PRESETS_RELATIVE), repo_dir(_PRESETS_RELATIVE)):
        if candidate is not None:
            return candidate
    raise ConfigError(
        "Caption presets not found. Set OPENCLIP_PRESETS_DIR to a directory of JSON presets."
    )


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
