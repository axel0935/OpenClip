"""Shared helpers for caption tests."""

from openclip.core.models import Clip, ClipSource, Segment, Transcript, Word

#: A small hand-made transcript: two sentences with a pause between them, so
#: sentence-ending punctuation, pause splitting and clamping can all be checked.
SENTENCE_WORDS = [
    Word(text="Hello", start=0.0, end=0.3),
    Word(text="there,", start=0.32, end=0.6),
    Word(text="friend.", start=0.62, end=0.95),
    Word(text="How", start=1.75, end=1.95),
    Word(text="are", start=1.97, end=2.1),
    Word(text="you", start=2.12, end=2.3),
    Word(text="today?", start=2.32, end=2.7),
]


def make_transcript() -> Transcript:
    """Build the reference transcript used across caption tests."""
    return Transcript(
        language="en",
        duration=10.0,
        model="base.en",
        segments=[
            Segment(
                id=0,
                start=0.0,
                end=2.7,
                text="Hello there, friend. How are you today?",
                words=SENTENCE_WORDS,
            )
        ],
    )


def make_clip(start: float = 0.0, end: float = 4.0) -> Clip:
    """Build a clip covering ``[start, end)`` of the reference transcript."""
    return Clip(
        id="clip-1",
        start=start,
        end=end,
        score=80.0,
        title="Reference clip",
        hook="A hook",
        hashtags=["#openclip"],
        rationale="Chosen for tests.",
        source=ClipSource.HEURISTIC,
    )


def diverse_transcript() -> Transcript:
    """A longer transcript with punctuation, pauses and a long word."""
    words = [
        Word(text="So", start=0.0, end=0.2),
        Word(text="here's", start=0.22, end=0.5),
        Word(text="the", start=0.52, end=0.62),
        Word(text="thing.", start=0.64, end=0.95),
        Word(text="Nobody", start=1.5, end=1.9),
        Word(text="told", start=1.92, end=2.05),
        Word(text="me", start=2.07, end=2.18),
        Word(text="that", start=2.2, end=2.4),
        Word(text="extraordinarily", start=2.42, end=3.1),
        Word(text="long", start=3.12, end=3.4),
        Word(text="words", start=3.42, end=3.7),
        Word(text="exist?", start=3.72, end=4.0),
    ]
    return Transcript(
        language="en",
        duration=30.0,
        model="base.en",
        segments=[Segment(id=0, start=0.0, end=4.0, text="So here's the thing.", words=words)],
    )
