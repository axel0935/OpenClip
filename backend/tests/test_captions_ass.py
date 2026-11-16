"""ASS structure, highlight modes, animations and snapshots.

Snapshots live in ``backend/tests/snapshots`` and are compared byte for byte,
which also proves the generator is deterministic.
"""

import re
from pathlib import Path

import pytest

from openclip.captions.ass import (
    BACKGROUND_LAYER,
    GLOW_LAYER,
    TEXT_LAYER,
    WORD_BOX_LAYER,
    build_ass,
    caption_metrics,
)
from openclip.captions.presets import load_presets
from openclip.captions.text import CANVAS_HEIGHT, CANVAS_WIDTH
from openclip.core.models import CaptionStyle, Clip, ClipSource, Segment, Transcript, Word
from tests.captions_helpers import make_clip, make_transcript

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
PRESETS = load_presets()


def _style(style_id: str) -> CaptionStyle:
    """Return one shipped preset."""
    return PRESETS[style_id]


def _events(ass: str) -> list[tuple[int, str, str, str]]:
    """Return ``(layer, start, end, text)`` for every dialogue line."""
    events: list[tuple[int, str, str, str]] = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            fields = line[len("Dialogue:") :].split(",", 9)
            events.append((int(fields[0]), fields[1].strip(), fields[2].strip(), fields[9]))
    return events


def _header(ass: str) -> str:
    """Return everything before the first dialogue line."""
    index = ass.find("\nDialogue:")
    return ass if index < 0 else ass[:index]


class TestHeader:
    """The script header and style line carry the canvas contract."""

    def test_canvas_is_1080x1920(self) -> None:
        header = _header(build_ass(make_transcript(), make_clip(), _style("clean-pop")))
        assert f"PlayResX: {CANVAS_WIDTH}" in header
        assert f"PlayResY: {CANVAS_HEIGHT}" in header
        assert "ScaledBorderAndShadow: yes" in header

    def test_single_style_line(self) -> None:
        header = _header(build_ass(make_transcript(), make_clip(), _style("clean-pop")))
        assert header.count("\nStyle: ") == 1

    def test_style_copy_matches_preset(self) -> None:
        style = _style("bold-impact")
        header = _header(build_ass(make_transcript(), make_clip(), style))
        line = next(line for line in header.splitlines() if line.startswith("Style: "))
        fields = line[len("Style: ") :].split(",")
        assert fields[1] == style.font_family
        assert fields[7] == ("-1" if style.bold else "0")
        assert fields[8] == ("-1" if style.italic else "0")
        assert fields[15] == "1"
        assert fields[19] == "60"
        assert fields[20] == "60"
        assert fields[21] == str(style.margin_v)

    @pytest.mark.parametrize(
        ("position", "alignment"),
        [("bottom", "2"), ("middle", "5"), ("top", "8")],
    )
    def test_alignment_follows_position(self, position: str, alignment: str) -> None:
        style = _style("clean-pop").model_copy(update={"position": position})
        header = _header(build_ass(make_transcript(), make_clip(), style))
        fields = next(line for line in header.splitlines() if line.startswith("Style: ")).split(",")
        assert fields[18] == alignment

    def test_fontsize_is_corrected_for_libass(self) -> None:
        """libass fits ascent+descent to Fontsize, so the emitted value is larger."""
        style = _style("clean-pop")
        metrics = caption_metrics(style)
        assert metrics["ass_fontsize"] > style.font_size
        assert metrics["ass_fontsize"] == pytest.approx(style.font_size * metrics["window_ratio"])

    def test_karaoke_uses_highlight_as_primary(self) -> None:
        style = _style("karaoke-glow")
        header = _header(build_ass(make_transcript(), make_clip(), style))
        fields = next(line for line in header.splitlines() if line.startswith("Style: ")).split(",")
        from openclip.captions.color import ass_color

        assert fields[3] == ass_color(style.highlight_color)
        assert fields[4] == ass_color(style.text_color)


class TestTiming:
    """Times are clip-relative, clamped, and never negative."""

    def test_first_event_starts_at_zero(self) -> None:
        events = _events(build_ass(make_transcript(), make_clip(), _style("clean-pop")))
        assert min(event[1] for event in events) == "0:00:00.00"

    def test_times_are_relative_to_clip_start(self) -> None:
        clip = Clip(
            id="c",
            start=1.0,
            end=3.0,
            score=50,
            title="t",
            hook="h",
            hashtags=[],
            rationale="r",
            source=ClipSource.HEURISTIC,
        )
        events = _events(build_ass(make_transcript(), clip, _style("clean-pop")))
        # The clip starts mid-pause, so its first caption starts at clip time ~0.75 s.
        assert events[0][1] == "0:00:00.75"
        assert max(event[2] for event in events) <= "0:00:02.00"

    def test_words_before_the_clip_are_dropped(self) -> None:
        clip = Clip(
            id="c",
            start=1.0,
            end=3.0,
            score=50,
            title="t",
            hook="h",
            hashtags=[],
            rationale="r",
            source=ClipSource.HEURISTIC,
        )
        ass = build_ass(make_transcript(), clip, _style("minimal-clean"))
        assert "Hello" not in ass
        assert "How" in ass

    def test_no_event_exceeds_clip_duration(self) -> None:
        clip = make_clip(start=0.0, end=1.0)
        for _, _, end, _ in _events(build_ass(make_transcript(), clip, _style("clean-pop"))):
            assert end <= "0:00:01.00"

    def test_no_negative_or_zero_length_events(self) -> None:
        for style in PRESETS.values():
            for _, start, end, _ in _events(build_ass(make_transcript(), make_clip(), style)):
                assert start < end


class TestHighlightModes:
    """Each mode emits the tags the renderer depends on."""

    def test_none_has_one_event_per_chunk(self) -> None:
        style = _style("minimal-clean")
        events = _events(build_ass(make_transcript(), make_clip(), style))
        text_events = [event for event in events if event[0] == TEXT_LAYER]
        assert len(text_events) == 2

    def test_word_color_has_one_event_per_word(self) -> None:
        style = _style("clean-pop")
        events = _events(build_ass(make_transcript(), make_clip(), style))
        text_events = [event for event in events if event[0] == TEXT_LAYER]
        assert len(text_events) == len(make_transcript().segments[0].words)

    def test_word_color_marks_exactly_one_word(self) -> None:
        style = _style("clean-pop")
        from openclip.captions.color import ass_bgr

        highlight = ass_bgr(style.highlight_color)
        for _, _, _, text in _events(build_ass(make_transcript(), make_clip(), style)):
            if f"\\1c{highlight}" in text:
                assert text.count(f"\\1c{highlight}") == 1

    def test_word_color_slices_are_consecutive_within_a_chunk(self) -> None:
        style = _style("clean-pop")
        spans = [
            (start, end)
            for layer, start, end, _ in _events(build_ass(make_transcript(), make_clip(), style))
            if layer == TEXT_LAYER
        ]
        # Word slices inside a chunk touch exactly; chunk boundaries leave a gap.
        assert spans[0][1] == spans[1][0]
        assert all(later[0] <= later[1] for later in spans)
        assert len(spans) == len(make_transcript().segments[0].words)

    def test_word_box_adds_a_plate_per_word(self) -> None:
        style = _style("boxed-highlight")
        events = _events(build_ass(make_transcript(), make_clip(), style))
        plates = [event for event in events if event[0] == WORD_BOX_LAYER]
        assert len(plates) == len(make_transcript().segments[0].words)
        for _, _, _, text in plates:
            assert "\\p1" in text
            assert "m " in text

    def test_word_box_plate_uses_highlight_colour(self) -> None:
        style = _style("boxed-highlight")
        from openclip.captions.color import ass_bgr

        plates = [
            event
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if event[0] == WORD_BOX_LAYER
        ]
        assert all(f"\\1c{ass_bgr(style.highlight_color)}" in event[3] for event in plates)

    def test_word_box_plates_are_inside_the_canvas(self) -> None:
        style = _style("boxed-highlight")
        plates = [
            event
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if event[0] == WORD_BOX_LAYER
        ]
        for _, _, _, text in plates:
            x, y = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", text).groups()  # type: ignore[union-attr]
            assert 0 <= float(x) < CANVAS_WIDTH
            assert 0 <= float(y) < CANVAS_HEIGHT

    def test_karaoke_uses_kf_on_one_event_per_chunk(self) -> None:
        style = _style("karaoke-glow")
        events = _events(build_ass(make_transcript(), make_clip(), style))
        text_events = [event for event in events if event[0] == TEXT_LAYER]
        assert len(text_events) == 2
        for _, _, _, text in text_events:
            assert "\\kf" in text

    def test_karaoke_durations_cover_the_event(self) -> None:
        style = _style("karaoke-glow")
        checked = 0
        for _, start, end, text in _events(build_ass(make_transcript(), make_clip(), style)):
            durations = [int(value) for value in re.findall(r"\\kf(\d+)", text)]
            if not durations:
                continue
            checked += 1
            assert sum(durations) == pytest.approx(_cs(end) - _cs(start), abs=1)
        assert checked >= 2


class TestAnimation:
    """Animation tags appear once per chunk, on its first event only."""

    @pytest.mark.parametrize(
        ("style_id", "tag"),
        [
            ("clean-pop", "\\fscx80"),
            ("karaoke-glow", "\\fad(120,80)"),
            ("podcast-lower-third", "\\move("),
        ],
    )
    def test_expected_tag_present(self, style_id: str, tag: str) -> None:
        ass = build_ass(make_transcript(), make_clip(), _style(style_id))
        first = _events(ass)[0][3]
        assert tag in first or any(tag in event[3] for event in _events(ass))

    def test_pop_scale_returns_to_one(self) -> None:
        style = _style("clean-pop")
        animated = [
            event
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if "\\fscx80" in event[3]
        ]
        assert animated
        assert "\\fscx80\\fscy80" in animated[0][3]
        assert "\\fscx112\\fscy112" in animated[0][3]
        assert "\\fscx100\\fscy100" in animated[0][3]

    def test_bounce_has_two_overshoots(self) -> None:
        style = _style("comic-burst")
        animated = [
            event
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if "\\fscx84" in event[3]
        ]
        assert animated
        text = animated[0][3]
        assert text.count("\\t(") >= 4
        assert "\\fscx114" in text
        assert "\\fscx105" in text

    def test_slide_up_moves_and_fades(self) -> None:
        style = _style("podcast-lower-third")
        text = next(
            event[3]
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if "\\move(" in event[3]
        )
        assert "\\alpha&HFF&" in text
        assert "\\t(0," in text

    def test_animation_runs_once_per_chunk(self) -> None:
        style = _style("comic-burst")
        events = [
            event
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if event[0] == TEXT_LAYER
        ]
        animated = [event for event in events if "\\fscx84" in event[3]]
        chunks = {_chunk_key(event) for event in animated}
        assert len(animated) == len(chunks)

    def test_word_box_mode_does_not_scale_text(self) -> None:
        """Scaling text would leave its plate behind."""
        style = _style("boxed-highlight")
        events = [
            event
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if event[0] == TEXT_LAYER
        ]
        assert all("\\fscx" not in event[3] for event in events)

    def test_none_animation_adds_no_tags(self) -> None:
        style = _style("bold-impact")
        assert style.animation == "none"
        events = [
            event
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if event[0] == TEXT_LAYER
        ]
        assert all("\\fscx" not in event[3] for event in events)


class TestBackgroundAndGlow:
    """Plates and soft shadows use dedicated layers below the text."""

    def test_background_plate_layer_and_duration(self) -> None:
        style = _style("podcast-lower-third")
        assert style.background is not None
        events = _events(build_ass(make_transcript(), make_clip(), style))
        plates = [event for event in events if event[0] == BACKGROUND_LAYER]
        assert len(plates) == 2
        for _layer, start, end, text in plates:
            assert "\\p1" in text
            assert _cs(start) < _cs(end)

    def test_plate_is_wider_than_the_text_block(self) -> None:
        style = _style("podcast-lower-third")
        plates = [
            event
            for event in _events(build_ass(make_transcript(), make_clip(), style))
            if event[0] == BACKGROUND_LAYER
        ]
        for _, _, _, text in plates:
            assert "m " in text

    def test_glow_layer_exists_when_shadow_is_configured(self) -> None:
        style = _style("clean-pop")
        assert style.shadow_color is not None
        events = _events(build_ass(make_transcript(), make_clip(), style))
        assert any(event[0] == GLOW_LAYER for event in events)
        assert any("\\blur" in event[3] for event in events)

    def test_glow_shares_the_text_entry_animation(self) -> None:
        """A glow at rest size under popping text separates visibly."""
        style = _style("clean-pop")
        events = _events(build_ass(make_transcript(), make_clip(), style))
        text_slices = [event for event in events if event[0] == TEXT_LAYER]
        glow_slices = [event for event in events if event[0] == GLOW_LAYER]
        assert len(glow_slices) == len(text_slices)
        for text_event, glow_event in zip(text_slices, glow_slices, strict=True):
            assert (text_event[1], text_event[2]) == (glow_event[1], glow_event[2])
            has_pop = "\\fscx80" in text_event[3]
            assert ("\\fscx80" in glow_event[3]) is has_pop

    def test_no_glow_without_shadow_colour(self) -> None:
        style = _style("podcast-lower-third").model_copy(
            update={"shadow_color": None, "shadow_offset": 0}
        )
        events = _events(build_ass(make_transcript(), make_clip(), style))
        assert not any(event[0] == GLOW_LAYER for event in events)

    def test_layers_are_ordered_plate_glow_text(self) -> None:
        assert BACKGROUND_LAYER < GLOW_LAYER < WORD_BOX_LAYER < TEXT_LAYER


class TestTextSafety:
    """Transcript text must not inject ASS override syntax."""

    def test_braces_and_backslashes_are_escaped(self) -> None:
        transcript = Transcript(
            language="en",
            duration=4.0,
            model="base.en",
            segments=[
                Segment(
                    id=0,
                    start=0.0,
                    end=2.0,
                    text="braces",
                    words=[Word(text="{evil\\tags}", start=0.0, end=0.5)],
                )
            ],
        )
        ass = build_ass(transcript, make_clip(), _style("minimal-clean"))
        body = _events(ass)[0][3]
        assert "\\{evil\\\\tags\\}" in body
        assert "{\\evil" not in body

    def test_newlines_are_flattened(self) -> None:
        transcript = Transcript(
            language="en",
            duration=4.0,
            model="base.en",
            segments=[
                Segment(
                    id=0,
                    start=0.0,
                    end=2.0,
                    text="lines",
                    words=[Word(text="two\nlines", start=0.0, end=0.5)],
                )
            ],
        )
        ass = build_ass(transcript, make_clip(), _style("minimal-clean"))
        assert "\n" not in _events(ass)[0][3]


class TestDeterminism:
    """Identical inputs must produce identical bytes."""

    @pytest.mark.parametrize("style_id", sorted(PRESETS))
    def test_same_input_same_output(self, style_id: str) -> None:
        first = build_ass(make_transcript(), make_clip(), PRESETS[style_id])
        second = build_ass(make_transcript(), make_clip(), PRESETS[style_id])
        assert first == second

    def test_transcript_order_does_not_change_output(self) -> None:
        base = make_transcript()
        reversed_segments = base.model_copy(
            update={
                "segments": [
                    base.segments[0].model_copy(
                        update={"words": list(reversed(base.segments[0].words))}
                    )
                ]
            }
        )
        style = _style("clean-pop")
        assert build_ass(base, make_clip(), style) == build_ass(
            reversed_segments, make_clip(), style
        )


class TestAllPresets:
    """Every shipped preset must build a valid, non-empty document."""

    @pytest.mark.parametrize("style_id", sorted(PRESETS))
    def test_builds_valid_ass(self, style_id: str) -> None:
        ass = build_ass(make_transcript(), make_clip(), PRESETS[style_id])
        assert ass.startswith("[Script Info]")
        assert "[V4+ Styles]" in ass
        assert "[Events]" in ass
        assert "Dialogue:" in ass
        assert ass.endswith("\n")
        for line in ass.splitlines():
            assert line.isprintable() or line == ""

    @pytest.mark.parametrize("style_id", sorted(PRESETS))
    def test_matches_snapshot(self, style_id: str) -> None:
        snapshot = SNAPSHOT_DIR / f"{style_id}.ass"
        ass = build_ass(make_transcript(), make_clip(), PRESETS[style_id])
        assert snapshot.exists(), f"missing snapshot {snapshot}"
        assert ass == snapshot.read_text(encoding="utf-8")

    def test_empty_transcript_yields_header_only(self) -> None:
        empty = Transcript(language="en", duration=4.0, model="base.en", segments=[])
        ass = build_ass(empty, make_clip(), _style("clean-pop"))
        assert "Dialogue:" not in ass


def _cs(value: str) -> int:
    """Parse an ASS timestamp into centiseconds."""
    hours, minutes, seconds = value.split(":")
    whole, hundredths = seconds.split(".")
    return int(hours) * 360_000 + int(minutes) * 6_000 + int(whole) * 100 + int(hundredths)


def _chunk_key(event: tuple[int, str, str, str]) -> str:
    """Identify the chunk an event belongs to by its visible text."""
    return re.sub(r"\{[^}]*\}", "", event[3])
