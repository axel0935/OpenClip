"""Implementation of ``openclip doctor`` and ``openclip models download``.

``doctor`` is a read-only self-check: it never downloads a model, never reads a
video, and only opens a socket to a *local* LLM endpoint (the one configured in
``OPENCLIP_LLM_BASE_URL`` or Ollama's default) with a one second budget. Every
check returns a status (OK / WARN / FAIL / INFO), one line of detail and a fix
hint when something is off. The exit code is 1 when at least one check fails.

External programs are always called with argument lists through
:func:`subprocess.run`; no shell is involved anywhere in this module.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import typer

from openclip import __version__
from openclip.core.config import Settings
from openclip.core.resources import install_mode, packaged_web_dir, repo_dir

CheckStatus = Literal["ok", "warn", "fail", "info"]

#: No single external probe may stall the report: ``doctor`` targets < 3 s.
_PROBE_TIMEOUT = 2.0
#: Local LLM probes stop after this long.
_LLM_TIMEOUT = 1.0
_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
_FREE_DISK_WARN_GIB = 5.0
_WHISPER_PROFILES = ("fast", "balanced", "quality")
_EXPECTED_PRESETS = 8
_YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"


@dataclass(frozen=True)
class Check:
    """The outcome of one diagnostic check."""

    name: str
    status: CheckStatus
    detail: str
    hint: str | None = None


@dataclass
class DoctorReport:
    """All checks collected for one ``doctor`` run."""

    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        """Append one check to the report."""
        self.checks.append(check)

    @property
    def failures(self) -> int:
        """Number of checks that failed."""
        return sum(1 for check in self.checks if check.status == "fail")

    @property
    def warnings(self) -> int:
        """Number of checks that only warned."""
        return sum(1 for check in self.checks if check.status == "warn")

    def exit_code(self) -> int:
        """Return 1 when anything failed, else 0."""
        return 1 if self.failures else 0

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable summary."""
        status = "fail" if self.failures else ("warn" if self.warnings else "ok")
        return {
            "openclip": __version__,
            "status": status,
            "failures": self.failures,
            "warnings": self.warnings,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "detail": check.detail,
                    "hint": check.hint,
                }
                for check in self.checks
            ],
        }


def _run(
    args: Sequence[str], timeout: float = _PROBE_TIMEOUT
) -> subprocess.CompletedProcess[str] | None:
    """Run one external command with an argument list and a short timeout."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _first_line(text: str) -> str:
    """Return the first non-empty line of a command's output."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _check_python() -> Check:
    """Report the interpreter that runs OpenClip.

    This is informational: ``requires-python = ">=3.12"`` means the installer
    already refused older interpreters, so the check cannot fail in practice.
    """
    version = platform.python_version()
    return Check("python", "ok", f"{version} ({sys.executable}), 3.12+ required")


def _check_install() -> Check:
    """Report the version and how OpenClip was installed."""
    mode = install_mode()
    label = "editable (source checkout)" if mode == "editable" else "installed wheel"
    return Check("openclip", "ok", f"{__version__}, {label}")


def _check_tool(name: str, executable: str) -> Check:
    """Check one external tool (ffmpeg or ffprobe) and its version."""
    resolved = shutil.which(executable)
    if resolved is None:
        return Check(
            name,
            "fail",
            f"'{executable}' not found on PATH",
            "install FFmpeg (it ships ffmpeg and ffprobe) and restart the shell",
        )
    result = _run([executable, "-version"])
    if result is None or result.returncode != 0:
        return Check(
            name,
            "fail",
            f"{resolved} did not answer '-version'",
            "reinstall FFmpeg; the binary looks broken",
        )
    line = _first_line(result.stdout)
    version = line.split(" Copyright")[0].replace(f"{name} version ", "").strip() or "unknown"
    return Check(name, "ok", f"{resolved} ({version})")


def _ffmpeg_names(executable: str) -> tuple[set[str], set[str]]:
    """Return the filter names and encoder names advertised by one FFmpeg."""
    filters: set[str] = set()
    encoders: set[str] = set()
    filters_run = _run([executable, "-hide_banner", "-filters"])
    if filters_run is not None and filters_run.returncode == 0:
        for line in filters_run.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                filters.add(parts[1])
    encoders_run = _run([executable, "-hide_banner", "-encoders"])
    if encoders_run is not None and encoders_run.returncode == 0:
        for line in encoders_run.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                encoders.add(parts[1])
    return filters, encoders


def _check_ffmpeg_capabilities(executable: str) -> list[Check]:
    """Check the subtitle filter and encoders, and report hardware encoders."""
    filters, encoders = _ffmpeg_names(executable)
    checks: list[Check] = []
    if "subtitles" in filters:
        checks.append(Check("subtitles filter", "ok", "libass subtitles filter available"))
    else:
        checks.append(
            Check(
                "subtitles filter",
                "fail",
                "FFmpeg has no 'subtitles' filter, so captions cannot be burned in",
                "install an FFmpeg build with libass (most distro packages have it)",
            )
        )
    missing = [name for name in ("libx264", "aac") if name not in encoders]
    if missing:
        checks.append(
            Check(
                "encoders",
                "fail",
                f"missing encoder(s): {', '.join(missing)}",
                "install an FFmpeg build with x264 and AAC support",
            )
        )
    else:
        checks.append(Check("encoders", "ok", "libx264 and aac present"))
    hardware = [name for name in ("h264_vaapi", "h264_qsv") if name in encoders]
    checks.append(
        Check(
            "hardware encoders",
            "info",
            ", ".join(hardware) if hardware else "none; software x264 will be used",
        )
    )
    return checks


def _module_present(module: str) -> bool:
    """Return True when a module can be imported."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _check_extra(name: str, module: str) -> Check:
    """Check that an optional extra is installed."""
    if _module_present(module):
        return Check(name, "ok", f"{module} available")
    return Check(
        name,
        "warn",
        f"{module} missing",
        f"install it with the '{name}' extra, e.g. pip install \"openclip[all]\"",
    )


def _whisper_model_dir(settings: Settings) -> Path:
    """Return the directory faster-whisper downloads weights into."""
    return settings.data_dir / "models"


def _whisper_cached(model_dir: Path, model_name: str) -> bool:
    """Return True when a Hugging Face snapshot for ``model_name`` exists."""
    if not model_dir.is_dir():
        return False
    for candidate in model_dir.glob(f"models--*faster-whisper-{model_name}"):
        if any(candidate.glob("snapshots/*")):
            return True
    return False


def _profile_models() -> dict[str, str]:
    """Map each concrete Whisper profile to its model name."""
    from openclip.transcribe.config import TranscribeConfig, resolve_profile

    models: dict[str, str] = {}
    for profile in _WHISPER_PROFILES:
        cfg = TranscribeConfig(profile=profile)  # type: ignore[arg-type]
        models[profile] = resolve_profile(cfg).model
    return models


def _check_whisper_models(settings: Settings) -> list[Check]:
    """Report the Whisper cache directory and which profiles are downloaded."""
    model_dir = _whisper_model_dir(settings)
    if not _module_present("faster_whisper"):
        return [
            Check(
                "whisper models",
                "warn",
                f"{model_dir} (faster-whisper missing, nothing can be downloaded)",
                "install the 'asr' extra: pip install \"openclip[asr]\"",
            )
        ]
    models = _profile_models()
    present = [
        f"{profile} ({name})"
        for profile, name in models.items()
        if _whisper_cached(model_dir, name)
    ]
    absent = [profile for profile, name in models.items() if not _whisper_cached(model_dir, name)]
    if present:
        first = Check("whisper models", "ok", f"{model_dir}: downloaded {', '.join(present)}")
    else:
        first = Check(
            "whisper models",
            "info",
            f"{model_dir}: no model downloaded yet",
            "the first transcription downloads one model, once",
        )
    checks = [first]
    if absent:
        checks.append(
            Check(
                "whisper profiles",
                "info",
                f"not downloaded: {', '.join(absent)}",
                f"optional: openclip models download --profile {absent[0]}",
            )
        )
    return checks


def _yunet_model_path(settings: Settings) -> Path:
    """Return the expected YuNet model path without importing numpy or OpenCV."""
    return _whisper_model_dir(settings) / _YUNET_FILENAME


def _check_yunet(settings: Settings) -> Check:
    """Check whether the YuNet face model is already on disk."""
    path = _yunet_model_path(settings)
    if path.is_file() and path.stat().st_size > 0:
        return Check("yunet model", "ok", f"{path} ({path.stat().st_size // 1024} KiB)")
    return Check(
        "yunet model",
        "info",
        f"{path} not downloaded yet",
        "fetched once on the first reframe; openclip models download --yunet prefetches it",
    )


def _check_presets() -> Check:
    """Check that the caption presets load from wherever they live."""
    from openclip.captions.presets import load_presets, presets_dir
    from openclip.core.errors import ConfigError

    try:
        directory = presets_dir()
        styles = load_presets()
    except ConfigError as exc:
        return Check(
            "caption presets",
            "fail",
            str(exc),
            "reinstall OpenClip or point OPENCLIP_PRESETS_DIR at the shipped presets",
        )
    if len(styles) == _EXPECTED_PRESETS:
        return Check("caption presets", "ok", f"{len(styles)} presets in {directory}")
    return Check(
        "caption presets",
        "warn",
        f"{len(styles)} presets in {directory} (expected {_EXPECTED_PRESETS})",
        "reinstall OpenClip or check OPENCLIP_PRESETS_DIR",
    )


def _check_fonts() -> Check:
    """Check that the flat font directory resolves and holds fonts only."""
    from openclip.captions.fonts import available_families, fonts_dir_or_none

    directory = fonts_dir_or_none()
    if directory is None:
        return Check(
            "caption fonts",
            "fail",
            "font directory not found",
            "reinstall OpenClip or set OPENCLIP_FONTS_DIR to a flat directory of TTF files",
        )
    families = available_families()
    try:
        stray = sorted(
            path.name for path in directory.iterdir() if path.suffix.lower() not in {".ttf", ".md"}
        )
    except OSError:
        stray = []
    detail = f"{directory} ({len(families)} families)"
    if stray:
        return Check(
            "caption fonts",
            "warn",
            f"{detail}; non-font entries in fontsdir: {', '.join(stray[:3])}",
            "keep fontsdir flat and font-only: libass tries to load every name in it",
        )
    return Check("caption fonts", "ok", detail)


def _check_web_bundle() -> Check:
    """Check whether a built web UI is available to ``openclip serve``."""
    override = os.environ.get("OPENCLIP_WEB_DIR")
    if override:
        candidate = Path(override).expanduser()
        if (candidate / "index.html").is_file():
            return Check("web UI", "ok", f"{candidate} (OPENCLIP_WEB_DIR)")
        return Check(
            "web UI",
            "warn",
            f"OPENCLIP_WEB_DIR={override!r} has no index.html",
            "point OPENCLIP_WEB_DIR at frontend/dist, or unset it",
        )
    packaged = packaged_web_dir()
    if packaged is not None:
        return Check("web UI", "ok", f"{packaged} (bundled in the package)")
    development = repo_dir("frontend/dist")
    if development is not None and (development / "index.html").is_file():
        return Check("web UI", "ok", f"{development} (development build)")
    return Check(
        "web UI",
        "warn",
        "no built web UI found, so openclip serve runs API-only",
        "run make build-web (needs pnpm) or install a release wheel that bundles the UI",
    )


def _memory_gib() -> tuple[float | None, float | None]:
    """Return ``(total, available)`` RAM in GiB, or ``(None, None)``."""
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values: dict[str, int] = {}
        try:
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    values[key.strip()] = int(parts[0])
        except OSError:
            values = {}
        total_kb = values.get("MemTotal")
        if total_kb:
            available_kb = values.get("MemAvailable", total_kb)
            return total_kb / 1024**2, available_kb / 1024**2
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None, None
    total = page * pages / 1024**3
    return total, total


def _existing_parent(path: Path) -> Path:
    """Return ``path`` or its nearest existing ancestor."""
    for candidate in [path, *path.parents]:
        if candidate.exists():
            return candidate
    return Path.cwd()


def _check_hardware(settings: Settings) -> list[Check]:
    """Report cores, RAM, free disk space and the profile ``auto`` resolves to."""
    cores = os.cpu_count() or 0
    total, available = _memory_gib()
    memory = "RAM unknown"
    if total is not None and available is not None:
        memory = f"{total:.1f} GiB RAM, {available:.1f} GiB available"
    target = _existing_parent(settings.data_dir)
    try:
        free_gib = shutil.disk_usage(target).free / 1024**3
    except OSError:
        free_gib = None
    disk = f"{free_gib:.0f} GiB free in {target}" if free_gib is not None else "disk space unknown"
    checks = [Check("hardware", "ok", f"{cores} logical cores, {memory}, {disk}")]
    if free_gib is not None and free_gib < _FREE_DISK_WARN_GIB:
        checks.append(
            Check(
                "disk space",
                "warn",
                f"only {free_gib:.1f} GiB free in {target}",
                "free space or set OPENCLIP_DATA_DIR to a larger volume (5 GiB recommended)",
            )
        )
    from openclip.transcribe.config import _auto_profile

    checks.append(
        Check("auto profile", "info", f"whisper profile 'auto' resolves to '{_auto_profile()}'")
    )
    return checks


def _llm_models(base_url: str) -> list[str] | None:
    """Return the model ids of an OpenAI-compatible endpoint, or ``None``."""
    import httpx

    try:
        with httpx.Client(timeout=_LLM_TIMEOUT) as client:
            response = client.get(f"{base_url.rstrip('/')}/models")
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    return [str(item["id"]) for item in data if isinstance(item, dict) and item.get("id")]


def _check_llm(settings: Settings) -> Check:
    """Report whether an LLM judge is reachable, without sending any user data."""
    configured = settings.llm_base_url
    base_url = configured or _OLLAMA_BASE_URL
    models = _llm_models(base_url)
    if models is None:
        if configured:
            return Check(
                "llm",
                "warn",
                f"OPENCLIP_LLM_BASE_URL={configured} did not answer /models within 1 s; "
                "analysis uses heuristic mode",
                "start the LLM server or clear OPENCLIP_LLM_BASE_URL",
            )
        return Check(
            "llm",
            "info",
            "no LLM endpoint answered on 127.0.0.1:11434; analysis uses heuristic mode",
            "optional: start Ollama or set OPENCLIP_LLM_BASE_URL",
        )
    shown = ", ".join(models[:5]) if models else "no model installed"
    origin = "" if configured else " (Ollama default)"
    if not models:
        return Check(
            "llm",
            "info",
            f"{base_url}{origin}: reachable, but {shown}; analysis uses heuristic mode",
            "optional: pull a model, e.g. ollama pull llama3.2",
        )
    return Check("llm", "ok", f"{base_url}{origin}: {shown}")


def run_checks(settings: Settings | None = None) -> DoctorReport:
    """Run every check and return the collected report."""
    settings = settings or Settings()
    report = DoctorReport()
    report.add(_check_python())
    report.add(_check_install())
    ffmpeg_check = _check_tool("ffmpeg", settings.ffmpeg_path)
    report.add(ffmpeg_check)
    report.add(_check_tool("ffprobe", settings.ffprobe_path))
    if ffmpeg_check.status == "ok":
        for check in _check_ffmpeg_capabilities(settings.ffmpeg_path):
            report.add(check)
    else:
        report.add(
            Check(
                "ffmpeg capabilities",
                "fail",
                "subtitles filter and encoders could not be probed",
                "install FFmpeg first, then re-run openclip doctor",
            )
        )
    report.add(_check_extra("asr", "faster_whisper"))
    report.add(_check_extra("vision", "cv2"))
    report.add(_check_extra("yt-dlp", "yt_dlp"))
    for check in _check_whisper_models(settings):
        report.add(check)
    report.add(_check_yunet(settings))
    report.add(_check_presets())
    report.add(_check_fonts())
    report.add(_check_web_bundle())
    for check in _check_hardware(settings):
        report.add(check)
    report.add(_check_llm(settings))
    return report


_SYMBOLS: dict[CheckStatus, str] = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "info": "INFO"}


def render_report(report: DoctorReport) -> str:
    """Render the human-readable report."""
    width = max((len(check.name) for check in report.checks), default=10)
    lines = [f"OpenClip doctor {__version__}", ""]
    for check in report.checks:
        lines.append(f"[{_SYMBOLS[check.status]}] {check.name:<{width}}  {check.detail}")
        if check.hint:
            lines.append(f"           {' ' * width}  hint: {check.hint}")
    lines.append("")
    if report.failures:
        summary = f"{report.failures} failure(s), {report.warnings} warning(s)"
    elif report.warnings:
        summary = f"no failures, {report.warnings} warning(s)"
    else:
        summary = "all checks passed"
    lines.append(f"Summary: {summary}")
    return "\n".join(lines)


def doctor(
    as_json: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON instead of a report.")
    ] = False,
) -> None:
    """Check the install: tools, extras, models, fonts, hardware and LLM."""
    report = run_checks()
    typer.echo(json.dumps(report.as_dict(), indent=2) if as_json else render_report(report))
    raise typer.Exit(code=report.exit_code())


def _download_whisper(profile: str, settings: Settings) -> Path:
    """Download one Whisper profile into the shared model cache."""
    from openclip.transcribe.config import TranscribeConfig, resolve_profile

    import importlib

    try:
        download_model = importlib.import_module("faster_whisper.utils").download_model
    except ImportError as exc:
        raise typer.BadParameter(
            'faster-whisper is not installed; install the asr extra (pip install "openclip[asr]")'
        ) from exc
    resolved = resolve_profile(TranscribeConfig(profile=profile))  # type: ignore[arg-type]
    model_dir = _whisper_model_dir(settings)
    model_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Downloading Whisper '{resolved.model}' into {model_dir} (once)...")
    download_model(resolved.model, cache_dir=str(model_dir))
    return model_dir


def _download_yunet(settings: Settings) -> Path:
    """Download and verify the YuNet face model."""
    try:
        from openclip.reframe.detector import yunet_model_path
    except ImportError as exc:
        raise typer.BadParameter(
            'the vision extra is not installed; install it with pip install "openclip[vision]"'
        ) from exc
    return yunet_model_path(settings)


def models_app() -> typer.Typer:
    """Build the ``openclip models`` sub-application."""
    app = typer.Typer(help="Download local models ahead of time.", no_args_is_help=True)

    @app.command("download")
    def download(
        profile: Annotated[
            str | None,
            typer.Option("--profile", help="Whisper profile: fast, balanced or quality."),
        ] = None,
        yunet: Annotated[
            bool, typer.Option("--yunet", help="Also fetch the YuNet face detector model.")
        ] = False,
    ) -> None:
        """Prefetch a Whisper model and/or the YuNet face model."""
        if profile is not None and profile not in _WHISPER_PROFILES:
            raise typer.BadParameter(f"profile must be one of: {', '.join(_WHISPER_PROFILES)}")
        if profile is None and not yunet:
            raise typer.BadParameter("nothing to do: pass --profile and/or --yunet")
        settings = Settings()
        if profile is not None:
            typer.echo(f"whisper: {_download_whisper(profile, settings)}")
        if yunet:
            typer.echo(f"yunet: {_download_yunet(settings)}")

    return app


__all__ = ["Check", "DoctorReport", "doctor", "models_app", "render_report", "run_checks"]
