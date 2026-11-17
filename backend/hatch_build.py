"""Hatch build hook that folds repository assets into the wheel.

The repository keeps shared data outside the Python package:

* ``presets/captions/*.json`` — caption styles
* ``assets/fonts/*.ttf``      — bundled fonts (flat; licences live in ``assets/licenses``)
* ``assets/licenses/*``       — font licence texts
* ``LICENSE``                 — project licence

An installed wheel cannot reach those paths, so this hook copies them into
``backend/src/openclip/data/`` before the wheel is built and removes the copy
afterwards. ``src/openclip/data`` and ``src/openclip/web`` are build outputs and
are git-ignored; a wheel built without a web bundle stays API-only, which keeps
a pure-Python build possible.

The hook finds the repository root by walking up from ``backend/``. A missing
asset root is not fatal: the wheel is then simply built without package data.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_DIRECTORIES: tuple[tuple[str, str], ...] = (
    ("presets/captions", "presets/captions"),
    ("assets/licenses", "licenses"),
)
_FILES: tuple[tuple[str, str], ...] = (
    ("assets/fonts/README.md", "README-fonts.md"),
    ("LICENSE", "LICENSE"),
)
#: ``fontsdir`` must stay flat and hold fonts only; libass tries every name.
_FONT_GLOB = "assets/fonts/*.ttf"


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for the repository root markers."""
    for candidate in [start, *start.parents]:
        if (candidate / "presets" / "captions").is_dir() and (candidate / "assets").is_dir():
            return candidate
    return None


class CustomBuildHook(BuildHookInterface):
    """Copy presets, fonts and licences into ``openclip/data`` while building."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Populate ``src/openclip/data`` before the archive is written."""
        self._copy_assets()

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        """Remove the staged copy so the source tree stays clean."""
        shutil.rmtree(Path(self.root) / "src" / "openclip" / "data", ignore_errors=True)

    def _copy_assets(self) -> None:
        repo = _find_repo_root(Path(self.root).resolve())
        package = Path(self.root) / "src" / "openclip"
        data_dir = package / "data"
        shutil.rmtree(data_dir, ignore_errors=True)
        if repo is None:
            self.app.display_warning(
                "openclip build hook: repository assets not found; "
                "the wheel will not bundle presets or fonts."
            )
            return
        staging = package / "data.staging"
        shutil.rmtree(staging, ignore_errors=True)
        for source_rel, target_rel in _DIRECTORIES:
            source = repo / source_rel
            if source.is_dir():
                shutil.copytree(source, staging / target_rel)
        fonts = sorted(repo.glob(_FONT_GLOB))
        if fonts:
            (staging / "fonts").mkdir(parents=True, exist_ok=True)
            for font in fonts:
                shutil.copy2(font, staging / "fonts" / font.name)
        for source_rel, target_rel in _FILES:
            source = repo / source_rel
            if source.is_file():
                staging.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, staging / target_rel)
        # One atomic move at the end: a half-copied tree must never be packaged.
        staging.replace(data_dir)
        self.app.display_info(f"openclip build hook: staged package data in {data_dir}")
