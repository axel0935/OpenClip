"""Grouping, timing and text-layout behaviour for caption chunks."""

import pytest

from openclip.captions.chunks import ChunkWord, build_chunks, clip_words
from openclip.captions.presets import load_presets
from openclip.captions.text import MAX_TEXT_WIDTH, layout_block, text_width
from openclip.core.models import CaptionStyle, Segment, Transcript, Word
from tests.captions_helpers import make_clip, make_transcript


def _words(pairs: list[tuple[str, float, float]]) -> list[ChunkWord]:
    """Build :class:`ChunkWord` entries from ``(text, start, end)`` triples."""
    return [ChunkWord(text=text, start=start, end=end) for text, start, end in pairs]


def _style(**overrides: object) -> CaptionStyle:
    """Return the default preset with selected fields overridden."""
    base = load_presets()["clean-pop"].model_dump()
    base.update(overrides)
    return CaptionStyle.model_validate(base)


class TestClipWords:
    """Words are clipped, clamped and shifted to clip time."""

    def test_shifts_to_clip_start(self) -> None:
        words = clip_words(make_transcript(), make_clip(start=1.0, end=4.0))
        assert words[0].text == "How"
        assert words[0].start == pytest.approx(0.75)

    def test_drops_words_outside_the_clip(self) -> None:
        words = clip_words(make_transcript(), make_clip(start=1.0, end=2.0))
        assert [word.text for word in words] == ["How", "are"]

    def test_clamps_straddling_word(self) -> None:
        transcript = Transcript(
            language="en",
            duration=5.0,
            model="base.en",
            segments=[
                Segment(
                    id=0,
                    start=0.0,
                    end=3.0,
                    text="straddles",
                    words=[Word(text="straddles", start=0.5, end=2.5)],
                )
            ],
        )
        words = clip_words(transcript, make_clip(start=1.0, end=2.0))
        assert words[0].start == 0.0
        assert words[0].end == pytest.approx(1.0)

    def test_empty_when_no_overlap(self) -> None:
        assert clip_words(make_transcript(), make_clip(start=6.0, end=8.0)) == []


class TestGrouping:
    """Chunks respect the word budget, punctuation, pauses and line limits."""

    def test_respects_words_per_caption(self) -> None:
        style = _style(words_per_caption=2)
        chunks = build_chunks(
            _words([(f"w{i}", i * 0.2, i * 0.2 + 0.15) for i in range(8)]), style, 4.0
        )
        assert all(len(chunk.words) <= 2 for chunk in chunks)

    def test_splits_on_sentence_end(self) -> None:
        style = _style(words_per_caption=6)
        words = _words([("First", 0.0, 0.2), ("one.", 0.22, 0.5), ("Second", 0.55, 0.8)])
        chunks = build_chunks(words, style, 2.0)
        assert [len(chunk.words) for chunk in chunks] == [2, 1]

    def test_splits_on_long_pause(self) -> None:
        style = _style(words_per_caption=6)
        words = _words([("before", 0.0, 0.3), ("after", 1.2, 1.5)])
        chunks = build_chunks(words, style, 2.0)
        assert len(chunks) == 2
        assert chunks[0].words[0].text == "before"

    def test_does_not_split_on_short_pause(self) -> None:
        style = _style(words_per_caption=6)
        words = _words([("one", 0.0, 0.3), ("two", 0.6, 0.9)])
        assert len(build_chunks(words, style, 2.0)) == 1

    def test_wraps_instead_of_splitting_when_it_fits(self) -> None:
        style = _style(words_per_caption=4, max_lines=2)
        words = _words(
            [("alpha", 0.0, 0.2), ("beta", 0.25, 0.45), ("gamma", 0.5, 0.7), ("delta", 0.75, 0.95)]
        )
        chunks = build_chunks(words, style, 2.0)
        assert len(chunks) == 1
        assert len(chunks[0].block.lines) <= 2

    def test_splits_when_lines_are_exceeded(self) -> None:
        style = _style(words_per_caption=6, max_lines=1, font_size=200)
        words = _words([(f"word{i}", i * 0.3, i * 0.3 + 0.25) for i in range(6)])
        chunks = build_chunks(words, style, 4.0)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.block.lines) <= 1

    def test_avoids_one_word_orphan(self) -> None:
        style = _style(words_per_caption=3)
        words = _words(
            [
                ("first", 0.0, 0.2),
                ("second", 0.22, 0.44),
                ("third", 0.46, 0.7),
                ("last", 0.75, 1.0),
            ]
        )
        chunks = build_chunks(words, style, 2.0)
        assert len(chunks[-1].words) >= 2

    def test_orphan_merge_never_crosses_a_sentence_end(self) -> None:
        style = _style(words_per_caption=3)
        words = _words(
            [
                ("first", 0.0, 0.2),
                ("second", 0.22, 0.44),
                ("third.", 0.46, 0.7),
                ("Next", 0.75, 1.0),
            ]
        )
        chunks = build_chunks(words, style, 2.0)
        assert chunks[0].words[-1].text == "third."
        assert chunks[-1].words[0].text == "Next"

    def test_never_exceeds_max_text_width(self) -> None:
        style = _style(words_per_caption=6, max_lines=2)
        words = _words([(f"long{i}", i * 0.4, i * 0.4 + 0.35) for i in range(6)])
        for chunk in build_chunks(words, style, 4.0):
            assert chunk.block.width <= MAX_TEXT_WIDTH + 1


class TestTiming:
    """Chunks are ordered, non-overlapping and clamped to the clip."""

    @pytest.mark.parametrize("style_id", list(load_presets()))
    def test_events_are_monotonic_and_non_overlapping(self, style_id: str) -> None:
        from openclip.captions.ass import build_ass

        style = load_presets()[style_id]
        ass = build_ass(make_transcript(), make_clip(), style)
        spans = _dialogue_spans(ass)
        assert spans
        by_layer: dict[int, list[tuple[int, int]]] = {}
        for layer, start, end in spans:
            by_layer.setdefault(layer, []).append((start, end))
        for layer_spans in by_layer.values():
            layer_spans.sort()
            for earlier, later in zip(layer_spans, layer_spans[1:], strict=False):
                assert later[0] >= earlier[1], f"overlap on layer: {earlier} {later}"
                assert earlier[1] > earlier[0], f"non-positive duration: {earlier}"

    def test_chunks_are_ordered(self) -> None:
        style = load_presets()["clean-pop"]
        chunks = build_chunks(clip_words(make_transcript(), make_clip()), style, 4.0)
        starts = [chunk.start for chunk in chunks]
        assert starts == sorted(starts)

    def test_chunk_end_covers_tail_but_not_next_start(self) -> None:
        style = _style(words_per_caption=2, max_lines=2)
        chunks = build_chunks(clip_words(make_transcript(), make_clip()), style, 4.0)
        assert chunks[0].end > chunks[0].words[-1].end
        assert chunks[0].end <= chunks[1].start

    def test_minimum_display_time(self) -> None:
        style = _style(words_per_caption=1)
        words = _words([("a", 0.0, 0.02), ("b", 0.5, 0.52)])
        chunks = build_chunks(words, style, 2.0)
        for chunk in chunks:
            assert chunk.end - chunk.start >= 0.3

    def test_never_exceeds_clip_duration(self) -> None:
        style = _style(words_per_caption=2)
        clip = make_clip(start=0.0, end=1.0)
        for chunk in build_chunks(clip_words(make_transcript(), clip), style, 1.0):
            assert chunk.end <= 1.0
            assert chunk.start >= 0.0

    def test_empty_input(self) -> None:
        assert build_chunks([], load_presets()["clean-pop"], 4.0) == []


class TestLayout:
    """Text measurement uses the real fonts and stays inside the safe width."""

    def test_measurement_scales_with_font_size(self) -> None:
        small = _style(font_size=40)
        large = _style(font_size=80)
        assert text_width("Hello", large) > text_width("Hello", small) * 1.5

    def test_uppercase_changes_width(self) -> None:
        lower = _style(uppercase=False)
        upper = _style(uppercase=True)
        assert text_width("hello", upper) != text_width("hello", lower)

    def test_layout_centres_every_line(self) -> None:
        style = _style(words_per_caption=6, max_lines=2)
        block = layout_block(["People", "will", "actually", "watch", "them", "and"], style)
        for line in block.lines:
            left = line.word_boxes[0].left
            right = line.word_boxes[-1].right
            assert left > 0
            assert abs((left + right) / 2 - 540) < 2

    def test_word_boxes_do_not_overlap(self) -> None:
        block = layout_block(["one", "two", "three"], load_presets()["clean-pop"])
        boxes = block.lines[0].word_boxes
        for earlier, later in zip(boxes, boxes[1:], strict=False):
            assert later.left >= earlier.right

    def test_line_heights_match_reported_block_height(self) -> None:
        style = _style(words_per_caption=6, max_lines=2)
        block = layout_block(["People", "will", "actually", "watch", "them", "and"], style)
        assert block.height == pytest.approx(block.line_height * len(block.lines))


def _dialogue_spans(ass: str) -> list[tuple[int, int, int]]:
    """Extract ``(layer, start_cs, end_cs)`` from an ASS document."""
    spans: list[tuple[int, int, int]] = []
    for line in ass.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line[len("Dialogue:") :].split(",", 9)
        spans.append((int(fields[0]), _cs(fields[1]), _cs(fields[2])))
    return spans


def _cs(value: str) -> int:
    """Parse an ASS timestamp into centiseconds."""
    hours, minutes, seconds = value.split(":")
    whole, hundredths = seconds.split(".")
    return int(hours) * 360_000 + int(minutes) * 6_000 + int(whole) * 100 + int(hundredths)
