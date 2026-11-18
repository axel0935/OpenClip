"""Regression tests for the three Phase 4 known bugs.

1. Hashtags must never contain the product name or generic filler/meta words.
2. A probably non-English source must produce a non-blocking warning.
3. ``analysis_mode=auto`` must skip the LLM judge when RAM is short.
"""

import json
from pathlib import Path

import pytest

from openclip.analyze.language import LANGUAGE_WARNING, check_transcript_language
from openclip.analyze.service import (
    LLM_MIN_AVAILABLE_BYTES,
    _clean_hashtags,
    _hashtags,
    _heuristic_metadata,
    llm_skip_reason,
    score_candidates,
)
from openclip.analyze.tags import BANNED_TAGS, is_banned_tag
from openclip.core.memory import available_memory_bytes, has_free_memory
from openclip.core.models import (
    Candidate,
    CandidateFeatures,
    ClipSource,
    JobOptions,
    Segment,
    Transcript,
    Word,
)

# ---------- bug 1: hashtags ----------

_OPENCLIP_TEXT = (
    "OpenClip is a tool that makes short clips from long videos. "
    "OpenClip runs locally and OpenClip never uploads your video."
)


def test_openclip_word_never_becomes_a_hashtag() -> None:
    assert "#openclip" not in _hashtags(_OPENCLIP_TEXT)


def test_meta_and_filler_words_never_become_hashtags() -> None:
    text = (
        "Watch this video on YouTube and subscribe to my channel. "
        "Like follow share trending viral fyp. Um uh okay yeah stuff things."
    )
    tags = [t.lstrip("#") for t in _hashtags(text)]
    assert tags, "topic words from the text should still be used"
    assert not any(is_banned_tag(word) for word in tags)


def test_hashtags_are_never_padded_with_a_fixed_tag() -> None:
    # A transcript with no usable topic word yields an empty list, not padding.
    assert _hashtags("Um uh okay yeah so yes") == []


def test_hashtags_skip_quantifiers_and_determiners() -> None:
    """Real recordings are full of 'most/every/whole'; those are not topics."""
    tags = _hashtags(
        "The most important thing about discipline is that you show up every day. "
        "Most people quit too early. That is the whole secret."
    )
    assert "#most" not in tags and "#every" not in tags and "#whole" not in tags
    assert "#discipline" in tags


def test_clean_hashtags_drops_llm_product_and_filler_tags() -> None:
    tags = _clean_hashtags(["#openclip", "#fyp", "#viral"], "focus beats talent")
    assert set(tags) == {"#focus", "#beats", "#talent"}


def test_clean_hashtags_keeps_a_short_clean_list_unpadded() -> None:
    assert _clean_hashtags(["#focus"], "um uh") == ["#focus"]


def test_heuristic_metadata_hashtags_are_clean() -> None:
    candidate = Candidate(
        id="candidate-001",
        start=0.0,
        end=30.0,
        text=_OPENCLIP_TEXT,
        features=CandidateFeatures(
            speech_rate_wps=3.0,
            audio_energy=0.5,
            question_count=0,
            scene_changes=0,
            hook_score=0.5,
        ),
        heuristic_score=50.0,
    )
    _title, _hook, tags, _rationale = _heuristic_metadata(candidate, 20.0)
    assert all(tag.lstrip("#").lower() not in BANNED_TAGS for tag in tags)


# ---------- bug 2: language safeguard ----------


def _transcript(text: str, language: str = "en") -> Transcript:
    words = [
        Word(text=word, start=i * 0.3, end=i * 0.3 + 0.28) for i, word in enumerate(text.split())
    ]
    end = max(0.1, len(words) * 0.3)
    return Transcript(
        language=language,
        duration=end,
        model="test",
        segments=[Segment(id=0, start=0.0, end=end, text=text, words=words)],
    )


_ENGLISH = (
    "The most important thing about discipline is that you show up every day and you do the "
    "work even when you do not feel like it. That is the whole secret. Most people quit too "
    "early because they want results immediately."
)
_GERMAN = (
    "Ich denke, das Wichtigste an Disziplin ist, dass du jeden Tag auftauchen und die Arbeit "
    "erledigen musst, auch wenn du keine Lust dazu hast. Das ist das ganze Geheimnis und die "
    "Leute geben zu frueh auf."
)
_SPANISH = (
    "Creo que lo mas importante de la disciplina es que tienes que aparecer todos los dias y "
    "hacer el trabajo aunque no tengas ganas. Ese es todo el secreto y la gente se rinde "
    "demasiado pronto porque quiere resultados inmediatos."
)


_CHINESE = (
    "我认为关于自律最重要的事情是你必须每天都出现并完成工作"
    "即使你不想做这就是全部的秘密大多数人都放弃得太早了"
)


@pytest.mark.parametrize(
    "language,text,reason",
    [
        ("en", _ENGLISH, None),
        ("de", _GERMAN, "looks_like_de"),
        ("es", _SPANISH, "looks_like_es"),
        ("zh", _CHINESE, "non_latin_script"),
    ],
)
def test_language_verdict(language: str, text: str, reason: str | None) -> None:
    verdict = check_transcript_language(_transcript(text, language))
    assert verdict.probably_english is (reason is None)
    assert verdict.reason == reason


def test_language_warning_is_non_blocking_and_carries_a_message() -> None:
    verdict = check_transcript_language(_transcript(_GERMAN, "de"))
    assert verdict.warning == LANGUAGE_WARNING
    assert "English" in LANGUAGE_WARNING


def test_language_check_never_raises_on_odd_input() -> None:
    empty = Transcript(language="en", duration=0.0, model="test", segments=[])
    assert check_transcript_language(empty).probably_english is True
    # Segments without words fall back to their text.
    from openclip.core.models import Segment as SegmentModel

    wordless = Transcript(
        language="en",
        duration=1.0,
        model="test",
        segments=[SegmentModel(id=0, start=0.0, end=1.0, text="hello there")],
    )
    assert check_transcript_language(wordless).probably_english is True


# ---------- bug 3: memory guard ----------


def test_meminfo_parsing(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 7000000 kB\nMemAvailable: 1048576 kB\n", encoding="utf-8")
    assert available_memory_bytes(meminfo) == 1024**3
    assert has_free_memory(1024**3, meminfo) is True
    assert has_free_memory(2 * 1024**3, meminfo) is False


def test_meminfo_unreadable_fails_soft(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert available_memory_bytes(missing) is None
    assert has_free_memory(64 * 1024**3, missing) is True  # unknown -> do not restrict
    garbage = tmp_path / "garbage"
    garbage.write_text("MemFree: lots\n", encoding="utf-8")
    assert available_memory_bytes(garbage) is None


def test_memory_guard_only_applies_to_auto_mode() -> None:
    low = LLM_MIN_AVAILABLE_BYTES - 1
    auto = llm_skip_reason(JobOptions(analysis_mode="auto"), available_bytes=low)
    assert auto is not None and "LLM judge was skipped" in auto
    assert llm_skip_reason(JobOptions(analysis_mode="llm"), available_bytes=low) is None
    assert llm_skip_reason(JobOptions(analysis_mode="heuristic"), available_bytes=low) is None
    assert llm_skip_reason(JobOptions(analysis_mode="auto"), available_bytes=8 * 1024**3) is None


class _RecordingProvider:
    """Minimal judge provider that records whether it was used."""

    def __init__(self) -> None:
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def chat(self, system: str, user: str) -> str:  # noqa: ARG002
        self.calls += 1
        payload = {
            "clips": [
                {
                    "id": "candidate-001",
                    "score": 88,
                    "title": "A solid title",
                    "hook": "This is a solid candidate window",
                    "hashtags": ["#one", "#two", "#three"],
                    "rationale": "Dense and clear.",
                }
            ]
        }
        return json.dumps(payload)

    def unload(self) -> None:
        """No-op: matches the real provider surface."""


def _candidate() -> Candidate:
    return Candidate(
        id="candidate-001",
        start=0.0,
        end=30.0,
        text="This is a solid candidate window with several sentences.",
        features=CandidateFeatures(
            speech_rate_wps=3.0,
            audio_energy=0.5,
            question_count=0,
            scene_changes=0,
            hook_score=0.5,
        ),
        heuristic_score=60.0,
    )


def test_auto_mode_skips_llm_when_memory_is_low() -> None:
    provider = _RecordingProvider()
    messages: list[str] = []
    clips = score_candidates(
        [_candidate()],
        _transcript(_ENGLISH),
        JobOptions(analysis_mode="auto"),
        provider=provider,
        progress=lambda _f, msg: messages.append(msg),
        available_bytes=1 * 1024**3,
    )
    assert provider.calls == 0, "the LLM must not be called below the guard"
    assert clips[0].source == ClipSource.HEURISTIC
    assert any("Low memory" in message for message in messages)


def test_auto_mode_uses_llm_with_enough_memory() -> None:
    provider = _RecordingProvider()
    clips = score_candidates(
        [_candidate()],
        _transcript(_ENGLISH),
        JobOptions(analysis_mode="auto"),
        provider=provider,
        available_bytes=8 * 1024**3,
    )
    assert provider.calls >= 1
    assert clips[0].source == ClipSource.LLM


def test_explicit_llm_mode_ignores_the_memory_guard() -> None:
    provider = _RecordingProvider()
    clips = score_candidates(
        [_candidate()],
        _transcript(_ENGLISH),
        JobOptions(analysis_mode="llm"),
        provider=provider,
        available_bytes=1 * 1024**3,
    )
    assert provider.calls >= 1
    assert clips[0].source == ClipSource.LLM
