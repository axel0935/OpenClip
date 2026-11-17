"""Locate bundled data files in a source checkout or in an installed wheel.

OpenClip ships three kinds of read-only data: caption presets, caption fonts and
the built web UI. A development checkout keeps them at the repository root
(``presets/captions``, ``assets/fonts``, ``frontend/dist``); a wheel keeps them
inside the package (``openclip/data/presets/captions``, ``openclip/data/fonts``,
``openclip/web``). Both layouts must work, so every lookup goes through the
helpers below instead of hard-coding a repository path.

Resolution order is always: explicit argument, environment variable, installed
package data, repository directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from openclip.core.errors import ConfigError

#: Markers that identify a source checkout (both must exist at its root).
REPO_MARKERS = ("presets/captions", "assets/fonts")


def package_dir() -> Path:
    """Return the installed ``openclip`` package directory."""
    return Path(__file__).resolve().parents[1]


def packaged_data_dir() -> Path | None:
    """Return ``<package>/data`` when the wheel shipped its data files."""
    candidate = package_dir() / "data"
    return candidate if candidate.is_dir() else None


def packaged_resource_dir(relative: str) -> Path | None:
    """Return a packaged data directory such as ``presets/captions``."""
    root = packaged_data_dir()
    if root is None:
        return None
    candidate = root / relative
    return candidate if candidate.is_dir() else None


def packaged_web_dir() -> Path | None:
    """Return ``<package>/web`` when the wheel contains a built web UI."""
    candidate = package_dir() / "web"
    return candidate if (candidate / "index.html").is_file() else None


def repo_root() -> Path | None:
    """Return the source checkout that contains this module, if any."""
    current = package_dir()
    for candidate in [current, *current.parents]:
        if all((candidate / marker).is_dir() for marker in REPO_MARKERS):
            return candidate
    return None


def repo_dir(relative: str) -> Path | None:
    """Return a repository-relative directory such as ``presets/captions``."""
    root = repo_root()
    if root is None:
        return None
    candidate = root / relative
    return candidate if candidate.is_dir() else None


def env_dir(env_var: str) -> Path | None:
    """Return the directory named by ``env_var``; raise when it is not usable."""
    value = os.environ.get(env_var)
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_dir():
        raise ConfigError(f"{env_var}={value!r} is not a directory.")
    return candidate


def install_mode() -> str:
    """Return ``"editable"`` for a source checkout, else ``"wheel"``.

    An editable install points ``openclip`` at ``<repo>/src/openclip`` (flat
    layout) or ``<repo>/backend/src/openclip`` (this repository); anything else
    is an installed wheel.
    """
    root = repo_root()
    if root is None:
        return "wheel"
    here = package_dir()
    for source in (root / "src" / "openclip", root / "backend" / "src" / "openclip"):
        if here == source:
            return "editable"
    return "wheel"
