"""Tests for ``openclip doctor`` (self-check) and ``openclip models download``.

Every external dependency is faked: subprocess results, filesystem probes and the
optional imports. The tests assert on the report contents and on the exit code,
never on a machine-specific detail, so they pass on CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openclip.cli import doctor_cmd
from openclip.cli.doctor_cmd import Check, DoctorReport, render_report
from openclip.cli.main import app
from openclip.core.config import Settings


def _settings(tmp_path: Path) -> Settings:
    """Return settings whose data directory is a temporary path."""
    return Settings(data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:1/v1")


class _Proc:
    """Stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _fake_runs() -> dict[tuple[str, ...], _Proc]:
    """Return canned outputs keyed by the argv tail that identifies each probe."""
    return {
        ("ffmpeg", "-version"): _Proc("ffmpeg version 8.0.1-3ubuntu2 Copyright (c) 2000-2025"),
        ("ffprobe", "-version"): _Proc("ffprobe version 8.0.1-3ubuntu2 Copyright (c) 2000-2025"),
        ("ffmpeg", "-hide_banner", "-filters"): _Proc(
            " .. ass    V->V  Render ASS subtitles onto input video using libass\n"
            " .. subtitles V->V Render text subtitles onto input video using libass\n"
        ),
        ("ffmpeg", "-hide_banner", "-encoders"): _Proc(
            " V....D libx264   libx264 H.264 / AVC\n"
            " A....D aac       AAC (Advanced Audio Coding)\n"
            " V....D h264_qsv  H.264 / AVC / MPEG-4 AVC (Intel Quick Sync)\n"
        ),
    }


@pytest.fixture()
def healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch every probe so the report is all OK/INFO on a healthy machine."""
    runs = _fake_runs()

    def fake_run(args, capture_output=True, text=True, timeout=None):  # noqa: ANN001, ANN202
        return runs.get(tuple(args), _Proc("", 0))

    monkeypatch.setattr(doctor_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor_cmd.shutil, "disk_usage", lambda _path: _Usage(50 * 1024**3))
    monkeypatch.setattr(doctor_cmd, "_module_present", lambda _module: True)
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: [])
    monkeypatch.setattr(
        doctor_cmd,
        "_check_whisper_models",
        lambda _settings: [
            Check("whisper models", "ok", "/data/models: downloaded fast (base.en)")
        ],
    )
    monkeypatch.setattr(doctor_cmd, "_check_presets", lambda: Check("caption presets", "ok", "8"))
    monkeypatch.setattr(doctor_cmd, "_check_fonts", lambda: Check("caption fonts", "ok", "/fonts"))
    monkeypatch.setattr(doctor_cmd, "_check_web_bundle", lambda: Check("web UI", "ok", "/web"))


class _Usage:
    """Minimal ``shutil.disk_usage`` result."""

    def __init__(self, free: int) -> None:
        self.free = free


def test_report_exit_code_is_zero_without_failures() -> None:
    """Only a failure makes the command exit non-zero."""
    report = DoctorReport([Check("a", "ok", ""), Check("b", "warn", ""), Check("c", "info", "")])
    assert report.failures == 0
    assert report.warnings == 1
    assert report.exit_code() == 0


def test_report_exit_code_is_one_with_a_failure() -> None:
    """A single FAIL flips the exit code to 1."""
    report = DoctorReport([Check("a", "ok", ""), Check("b", "fail", "boom", "fix it")])
    assert report.exit_code() == 1


def test_json_report_shape() -> None:
    """The JSON payload carries status, counts and every check."""
    report = DoctorReport([Check("a", "ok", "fine"), Check("b", "fail", "bad", "do x")])
    payload = report.as_dict()
    assert payload["status"] == "fail"
    assert payload["failures"] == 1
    assert payload["warnings"] == 0
    checks = payload["checks"]
    assert isinstance(checks, list)
    assert checks[0]["name"] == "a"
    assert checks[1]["hint"] == "do x"
    assert json.dumps(payload)


def test_render_report_shows_status_and_hint() -> None:
    """The human report names every check and prints hints below it."""
    report = DoctorReport([Check("ffmpeg", "fail", "not found", "install FFmpeg")])
    text = render_report(report)
    assert "[FAIL]" in text
    assert "ffmpeg" in text
    assert "hint: install FFmpeg" in text
    assert "1 failure(s)" in text


def test_healthy_machine_has_no_failures(healthy: None, tmp_path: Path) -> None:
    """With every probe faked, the real run still reports only OK/INFO/WARN."""
    report = doctor_cmd.run_checks(_settings(tmp_path))
    assert report.failures == 0
    names = [check.name for check in report.checks]
    assert "ffmpeg" in names
    assert "ffprobe" in names
    assert "subtitles filter" in names
    assert "encoders" in names


def test_missing_ffmpeg_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing ffmpeg is a FAIL with an install hint, not a traceback."""
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda _name: None)
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: None)
    report = doctor_cmd.run_checks(_settings(tmp_path))
    ffmpeg = next(check for check in report.checks if check.name == "ffmpeg")
    assert ffmpeg.status == "fail"
    assert ffmpeg.hint and "FFmpeg" in ffmpeg.hint
    assert report.exit_code() == 1
    assert any(
        check.name == "ffmpeg capabilities" and check.status == "fail" for check in report.checks
    )


def test_broken_ffmpeg_binary_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An ffmpeg that cannot answer ``-version`` is reported as broken."""
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor_cmd.subprocess, "run", lambda *a, **k: _Proc("", returncode=1))
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: None)
    report = doctor_cmd.run_checks(_settings(tmp_path))
    ffmpeg = next(check for check in report.checks if check.name == "ffmpeg")
    assert ffmpeg.status == "fail"
    assert "did not answer" in ffmpeg.detail


def test_missing_subtitles_filter_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FFmpeg without libass cannot burn captions, so doctor must fail."""
    runs = _fake_runs()
    runs[("ffmpeg", "-hide_banner", "-filters")] = _Proc(" .. scale V->V Scale the input video\n")

    def fake_run(args, capture_output=True, text=True, timeout=None):  # noqa: ANN001, ANN202
        return runs.get(tuple(args), _Proc("", 0))

    monkeypatch.setattr(doctor_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: None)
    report = doctor_cmd.run_checks(_settings(tmp_path))
    subs = next(check for check in report.checks if check.name == "subtitles filter")
    assert subs.status == "fail"
    assert report.exit_code() == 1


def test_missing_encoders_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """libx264 and aac are required for the render stage."""
    runs = _fake_runs()
    runs[("ffmpeg", "-hide_banner", "-encoders")] = _Proc(" A....D opus  Opus\n")

    def fake_run(args, capture_output=True, text=True, timeout=None):  # noqa: ANN001, ANN202
        return runs.get(tuple(args), _Proc("", 0))

    monkeypatch.setattr(doctor_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: None)
    report = doctor_cmd.run_checks(_settings(tmp_path))
    encoders = next(check for check in report.checks if check.name == "encoders")
    assert encoders.status == "fail"
    assert "libx264" in encoders.detail and "aac" in encoders.detail


def test_hardware_encoders_are_info_only(healthy: None, tmp_path: Path) -> None:
    """Hardware encoders never turn the report red."""
    report = doctor_cmd.run_checks(_settings(tmp_path))
    hardware = next(check for check in report.checks if check.name == "hardware encoders")
    assert hardware.status == "info"


def test_missing_extras_only_warn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Absent optional extras are WARN, so a base install still passes."""
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor_cmd, "_module_present", lambda _module: False)
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: None)
    report = doctor_cmd.run_checks(_settings(tmp_path))
    extras = {check.name: check for check in report.checks}
    assert extras["asr"].status == "warn"
    assert extras["vision"].status == "warn"
    assert extras["yt-dlp"].status == "warn"
    assert report.exit_code() == 0


def test_whisper_models_report_downloaded_profiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only profiles with a real snapshot count as downloaded."""
    model_dir = tmp_path / "data" / "models"
    (model_dir / "models--Systran--faster-whisper-base.en" / "snapshots" / "abc").mkdir(
        parents=True
    )
    monkeypatch.setattr(doctor_cmd, "_module_present", lambda _module: True)
    checks = doctor_cmd._check_whisper_models(Settings(data_dir=tmp_path / "data"))
    combined = " ".join(check.detail for check in checks)
    assert "fast (base.en)" in combined
    assert "balanced" in combined
    assert checks[0].status == "ok"


def test_whisper_models_without_asr_extra(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without faster-whisper the models check explains why nothing can download."""
    monkeypatch.setattr(doctor_cmd, "_module_present", lambda _module: False)
    checks = doctor_cmd._check_whisper_models(Settings(data_dir=tmp_path / "data"))
    assert checks[0].status == "warn"
    assert "faster-whisper missing" in checks[0].detail


def test_yunet_model_detection(tmp_path: Path) -> None:
    """The YuNet check only looks at the data dir and never downloads."""
    settings = Settings(data_dir=tmp_path / "data")
    assert doctor_cmd._check_yunet(settings).status == "info"
    model = doctor_cmd._yunet_model_path(settings)
    model.parent.mkdir(parents=True)
    model.write_bytes(b"x" * 2048)
    check = doctor_cmd._check_yunet(settings)
    assert check.status == "ok"
    assert "2 KiB" in check.detail


def test_presets_and_fonts_really_load() -> None:
    """The shipped presets and fonts resolve in a checkout and in a wheel."""
    presets = doctor_cmd._check_presets()
    fonts = doctor_cmd._check_fonts()
    assert presets.status == "ok"
    assert fonts.status == "ok"


def test_fonts_check_warns_on_stray_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A non-font file inside fontsdir is a WARN: libass would try to load it."""
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "A-Regular.ttf").write_bytes(b"x")
    (fonts / "licence.txt").write_text("OFL")
    monkeypatch.setattr("openclip.captions.fonts.fonts_dir_or_none", lambda: fonts)
    check = doctor_cmd._check_fonts()
    assert check.status == "warn"
    assert "licence.txt" in check.detail


def test_web_bundle_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``OPENCLIP_WEB_DIR`` wins when it contains an index.html."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    monkeypatch.setenv("OPENCLIP_WEB_DIR", str(dist))
    check = doctor_cmd._check_web_bundle()
    assert check.status == "ok"
    assert str(dist) in check.detail


def test_web_bundle_env_override_without_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale override is a WARN, not a crash."""
    monkeypatch.setenv("OPENCLIP_WEB_DIR", str(tmp_path / "missing"))
    check = doctor_cmd._check_web_bundle()
    assert check.status == "warn"


def test_disk_space_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Less than 5 GiB free is a WARN."""
    monkeypatch.setattr(doctor_cmd.shutil, "disk_usage", lambda _path: _Usage(1024**3))
    checks = doctor_cmd._check_hardware(Settings(data_dir=tmp_path / "data"))
    disk = next(check for check in checks if check.name == "disk space")
    assert disk.status == "warn"


def test_llm_reachable_lists_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reachable endpoint shows its models and passes."""
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: ["llama3.2", "qwen2.5"])
    check = doctor_cmd._check_llm(Settings(llm_base_url="http://127.0.0.1:11434/v1"))
    assert check.status == "ok"
    assert "llama3.2" in check.detail


def test_llm_reachable_without_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """An endpoint with no model installed stays heuristic and says so."""
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: [])
    check = doctor_cmd._check_llm(Settings(llm_base_url="http://127.0.0.1:11434/v1"))
    assert check.status == "info"
    assert "heuristic" in check.detail


def test_llm_unreachable_is_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    """No endpoint and no override is INFO: heuristic mode is a supported default."""
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: None)
    check = doctor_cmd._check_llm(Settings(llm_base_url=None))
    assert check.status == "info"
    assert "heuristic" in check.detail


def test_llm_configured_but_down_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured endpoint that does not answer is a WARN with a fix hint."""
    monkeypatch.setattr(doctor_cmd, "_llm_models", lambda _url: None)
    check = doctor_cmd._check_llm(Settings(llm_base_url="http://127.0.0.1:9/v1"))
    assert check.status == "warn"
    assert check.hint


def test_cli_exit_code_and_json(healthy: None) -> None:
    """``openclip doctor --json`` prints JSON and exits 0 on a healthy machine."""
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] in {"ok", "warn"}
    assert payload["failures"] == 0


def test_cli_exit_code_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing check makes the command exit 1."""

    def fake_run_checks(_settings=None):  # noqa: ANN001, ANN202
        return DoctorReport([Check("ffmpeg", "fail", "missing", "install it")])

    monkeypatch.setattr(doctor_cmd, "run_checks", fake_run_checks)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL]" in result.stdout
    assert "Summary: 1 failure(s)" in result.stdout


def test_models_download_requires_a_target() -> None:
    """``models download`` without a selector is a usage error."""
    result = CliRunner().invoke(app, ["models", "download"])
    assert result.exit_code != 0


def test_models_download_rejects_unknown_profile() -> None:
    """Only fast/balanced/quality are valid profiles."""
    result = CliRunner().invoke(app, ["models", "download", "--profile", "medium"])
    assert result.exit_code != 0
    assert "fast" in result.stdout or "fast" in result.stderr


def test_models_download_uses_the_loader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The thin wrapper calls faster-whisper's own downloader, nothing else."""
    calls: list[tuple[str, str]] = []

    class _Utils:
        @staticmethod
        def download_model(model: str, cache_dir: str) -> str:  # noqa: ANN001
            calls.append((model, cache_dir))
            return cache_dir

    import sys
    import types

    fake_utils = types.ModuleType("faster_whisper.utils")
    fake_utils.download_model = _Utils.download_model  # type: ignore[attr-defined]
    module = types.ModuleType("faster_whisper")
    module.utils = fake_utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", fake_utils)
    monkeypatch.setattr(doctor_cmd, "Settings", lambda: Settings(data_dir=tmp_path / "data"))
    result = CliRunner().invoke(app, ["models", "download", "--profile", "fast"])
    assert result.exit_code == 0, result.stdout
    assert calls == [("base.en", str(tmp_path / "data" / "models"))]
