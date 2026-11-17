"""Unit tests for colour conversion, timestamps and preset/font resolution."""

import json
from pathlib import Path

import pytest

from openclip.captions.color import ass_alpha, ass_bgr, ass_color, color_tags, parse_color
from openclip.captions.fonts import (
    available_families,
    ffmpeg_subtitles_filter,
    font_path,
    fonts_dir,
)
from openclip.captions.presets import load_presets, presets_dir
from openclip.captions.timecode import ass_time_from_seconds, to_centiseconds
from openclip.core.errors import ConfigError


class TestColor:
    """Hex to ASS conversion. ASS alpha is inverted: 00 is opaque."""

    def test_parse_opaque_hex(self) -> None:
        assert parse_color("#FFD400") == (255, 212, 0, 255)

    def test_parse_hex_with_alpha(self) -> None:
        assert parse_color("#0B0C0ECC") == (11, 12, 14, 204)

    @pytest.mark.parametrize("value", ["", "FFFFFF", "#FFF", "#GGGGGG", "#1234567"])
    def test_reject_invalid(self, value: str) -> None:
        with pytest.raises(ConfigError):
            parse_color(value)

    def test_ass_bgr_reverses_channels(self) -> None:
        assert ass_bgr("#FFD400") == "&H00D4FF&"

    def test_ass_alpha_is_inverted(self) -> None:
        assert ass_alpha("#000000FF") == "&H00&"
        assert ass_alpha("#00000000") == "&HFF&"
        assert ass_alpha("#00000080") == "&H7F&"

    def test_ass_color_packs_alpha_first(self) -> None:
        assert ass_color("#FFD400") == "&H0000D4FF"
        assert ass_color("#0B0C0ECC") == "&H330E0C0B"

    def test_color_tags_sets_primary(self) -> None:
        assert color_tags("#FFFFFF") == "\\1c&HFFFFFF&\\1a&H00&"

    def test_color_tags_can_target_other_channels(self) -> None:
        assert color_tags("#000000", index=3).startswith("\\3c&H000000&")


class TestTimecode:
    """ASS timestamps use ``H:MM:SS.cc`` with centisecond precision."""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "0:00:00.00"),
            (0.12, "0:00:00.12"),
            (1.5, "0:00:01.50"),
            (59.99, "0:00:59.99"),
            (60.0, "0:01:00.00"),
            (3661.25, "1:01:01.25"),
        ],
    )
    def test_format(self, seconds: float, expected: str) -> None:
        assert ass_time_from_seconds(seconds) == expected

    def test_negative_is_clamped(self) -> None:
        assert ass_time_from_seconds(-5.0) == "0:00:00.00"

    def test_rounding_is_to_nearest_centisecond(self) -> None:
        assert to_centiseconds(1.234) == 123
        assert to_centiseconds(1.236) == 124


class TestFonts:
    """Every preset family must resolve to a bundled, flat TTF file."""

    def test_repo_fonts_dir_is_flat(self) -> None:
        root = fonts_dir()
        assert root.is_dir()
        assert not any(child.is_dir() for child in root.iterdir() if child.name != "__pycache__")

    @pytest.mark.parametrize(
        ("family", "bold"),
        [
            ("Montserrat", True),
            ("Montserrat", False),
            ("Anton", False),
            ("Poppins", True),
            ("Archivo Black", False),
            ("Bebas Neue", False),
            ("Bangers", False),
            ("Inter", False),
            ("Oswald", True),
        ],
    )
    def test_family_resolves(self, family: str, bold: bool) -> None:
        path = font_path(family, bold)
        assert path is not None and path.suffix == ".ttf"

    def test_missing_family_returns_none(self) -> None:
        assert font_path("Comic Sans MS") is None

    def test_weight_selection(self) -> None:
        bold = font_path("Montserrat", True)
        regular = font_path("Montserrat", False)
        assert bold is not None and bold.name.endswith("Bold.ttf")
        assert regular is not None and regular.name.endswith("Regular.ttf")

    def test_all_preset_families_are_bundled(self) -> None:
        bundled = {family.lower() for family in available_families()}
        for style in load_presets().values():
            assert style.font_family.lower() in bundled, style.font_family

    def test_every_family_has_a_licence(self) -> None:
        # Licence texts live in a sibling directory: libass scans every name in
        # ``fontsdir``, so the flat font directory must contain TTFs only.
        licences_dir = fonts_dir().parent / "licenses"
        families = available_families()
        assert families
        licences = list(licences_dir.glob("*OFL.txt")) + list(licences_dir.glob("*LICENSE*"))
        assert len(licences) >= len(families)
        assert not list(fonts_dir().glob("*.txt"))

    def test_env_override_must_exist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENCLIP_FONTS_DIR", "/nonexistent-openclip-fonts")
        with pytest.raises(ConfigError):
            fonts_dir()


class TestSubtitlesFilter:
    """The FFmpeg filter string must survive spaces, colons and apostrophes."""

    def test_includes_filename_and_fontsdir(self) -> None:
        result = ffmpeg_subtitles_filter(Path("/tmp/a.ass"), Path("/tmp/fonts"))
        assert result == "subtitles=filename='/tmp/a.ass':fontsdir='/tmp/fonts'"

    def test_escapes_special_characters(self) -> None:
        result = ffmpeg_subtitles_filter(Path("/tmp/o'clock:1.ass"), Path("/tmp/f dir"))
        assert "o\\'clock\\:1.ass" in result
        assert "f dir" in result

    def test_uses_env_fonts_dir_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("OPENCLIP_FONTS_DIR", str(tmp_path))
        assert f"fontsdir='{tmp_path}'" in ffmpeg_subtitles_filter(Path("/tmp/a.ass"))


class TestPresets:
    """All eight shipped presets must validate against the model."""

    def test_loads_eight_presets(self) -> None:
        styles = load_presets()
        assert len(styles) == 8
        assert list(styles) == sorted(styles)

    def test_keys_match_ids(self) -> None:
        for key, style in load_presets().items():
            assert key == style.id

    def test_expected_ids(self) -> None:
        assert set(load_presets()) == {
            "bold-impact",
            "boxed-highlight",
            "clean-pop",
            "comic-burst",
            "karaoke-glow",
            "minimal-clean",
            "neon-night",
            "podcast-lower-third",
        }

    def test_invalid_json_reports_the_file(self, tmp_path: Path) -> None:
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="broken.json"):
            load_presets(tmp_path)

    def test_invalid_field_reports_the_field(self, tmp_path: Path) -> None:
        payload = json.loads((presets_dir() / "clean-pop.json").read_text(encoding="utf-8"))
        payload["font_size"] = 0
        (tmp_path / "clean-pop.json").write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ConfigError, match="font_size"):
            load_presets(tmp_path)

    def test_missing_directory_is_an_error(self) -> None:
        with pytest.raises(ConfigError):
            load_presets("/nonexistent-openclip-presets")

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        source = presets_dir() / "clean-pop.json"
        (tmp_path / "clean-pop.json").write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
        monkeypatch.setenv("OPENCLIP_PRESETS_DIR", str(tmp_path))
        assert list(load_presets()) == ["clean-pop"]
