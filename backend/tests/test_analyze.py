"""Tests for the highlight analysis engine."""

import json
from pathlib import Path
from typing import Any

import pytest

from openclip.analyze.candidates import (
    _content_words,
    _cosine,
    _generate_windows,
    _split_sentences,
    find_candidates,
)
from openclip.analyze.features import (
    FeatureContext,
    _detect_scene_changes,
    _rms_per_second,
    hook_score,
    is_question,
    window_features,
)
from openclip.analyze.llm import (
    LLMClipJudgment,
    _parse_judgments,
    judge_candidates,
)
from openclip.analyze.scoring import (
    duration_score,
    filler_density,
    heuristic_score,
    longest_word_gap,
    speech_rate_score,
)
from openclip.analyze.select import select_clips, sort_chronologically
from openclip.analyze.service import _heuristic_metadata, analyze, score_candidates
from openclip.core.config import Settings
from openclip.core.errors import AnalysisError
from openclip.core.models import (
    Candidate,
    CandidateFeatures,
    Clip,
    ClipSource,
    JobOptions,
    MediaInfo,
    Transcript,
    Word,
)
from tests.media import make_tone_wav, make_video_with_audio

GOLDEN_DIR = Path(__file__).parent / "golden"


def _word(text: str, start: float, end: float) -> Word:
    return Word(text=text, start=start, end=end, probability=0.9)


def _segment(seg_id: int, start: float, end: float, text: str, wps: float = 3.0) -> Any:
    """Build a Segment with evenly spaced word timings."""
    from openclip.core.models import Segment

    tokens = text.split()
    step = (end - start) / max(1, len(tokens))
    words = []
    t = start
    for tok in tokens:
        wend = min(t + step, end)
        words.append(_word(tok, round(t, 3), round(wend, 3)))
        t = wend
    return Segment(id=seg_id, start=start, end=end, text=text, words=words)


def _transcript(segments: list[Any], duration: float) -> Transcript:
    return Transcript(language="en", duration=duration, model="test", segments=segments)


def _media(path: Path) -> MediaInfo:
    return MediaInfo(
        path=str(path),
        duration=100.0,
        width=320,
        height=240,
        fps=15.0,
        has_audio=True,
    )


def _features(**overrides: Any) -> CandidateFeatures:
    base = {
        "speech_rate_wps": 3.0,
        "audio_energy": 0.6,
        "question_count": 0,
        "scene_changes": 0,
        "hook_score": 0.5,
    }
    base.update(overrides)
    return CandidateFeatures(**base)


def _candidate(
    cid: str = "candidate-001",
    start: float = 0.0,
    end: float = 30.0,
    score: float = 50.0,
    text: str = "This is a test candidate with enough words to be realistic.",
) -> Candidate:
    return Candidate(
        id=cid,
        start=start,
        end=end,
        text=text,
        features=_features(),
        heuristic_score=score,
    )


def _clip(cid: str, start: float, end: float, score: float) -> Clip:
    return Clip(
        id=cid,
        start=start,
        end=end,
        score=score,
        title="t",
        hook="h",
        hashtags=["#a", "#b", "#c"],
        rationale="r",
        source=ClipSource.HEURISTIC,
    )


# ---------- sentence splitting ----------


def test_split_sentences_on_punctuation() -> None:
    seg = _segment(0, 0.0, 10.0, "First sentence here. Second one? Third ends!")
    sents = _split_sentences(_transcript([seg], 10.0))
    assert len(sents) == 3
    assert sents[0].text.startswith("First")
    assert sents[1].text.startswith("Second")
    assert sents[2].text.startswith("Third")


def test_split_sentences_segment_tail() -> None:
    seg = _segment(0, 0.0, 5.0, "No punctuation at the end")
    sents = _split_sentences(_transcript([seg], 5.0))
    assert len(sents) == 1
    assert sents[0].end == pytest.approx(5.0)


def test_split_sentences_without_words() -> None:
    from openclip.core.models import Segment

    seg = Segment(id=0, start=0.0, end=4.0, text="A whole segment without words.", words=[])
    sents = _split_sentences(_transcript([seg], 4.0))
    assert len(sents) == 1
    assert sents[0].text == "A whole segment without words."


# ---------- windows ----------


def test_generate_windows_respects_bounds() -> None:
    # 10 sentences of 10 s each -> 100 s of speech.
    segs = [
        _segment(i, i * 10.0, (i + 1) * 10.0, f"Sentence number {i} is here now.")
        for i in range(10)
    ]
    sents = _split_sentences(_transcript(segs, 100.0))
    windows = _generate_windows(sents, min_s=20.0, max_s=50.0)
    assert windows
    for first, last in windows:
        duration = sents[last].end - sents[first].start
        assert 20.0 <= duration <= 50.0 + 1e-6


def test_cosine_and_content_words() -> None:
    a = _content_words("the quick brown fox jumps")
    b = _content_words("quick brown fox runs")
    assert _cosine(a, b) > 0.3
    assert _cosine(a, {"zebra", "xylophone"}) == 0.0


# ---------- features ----------


def test_is_question() -> None:
    assert is_question("What is the best camera?")
    assert is_question("How do you grow views")
    assert not is_question("This is a statement.")


def test_hook_score_rules() -> None:
    strong = hook_score("Did you know 90 percent of creators never find this secret setting?")
    weak = hook_score("Um so like basically I was just thinking you know and stuff.")
    greeting = hook_score("Hey guys welcome back to the channel today.")
    assert strong > 0.6
    assert weak < 0.3
    assert greeting < strong
    assert 0.0 <= hook_score("") <= 1.0


def test_speech_rate_and_gap_helpers() -> None:
    assert speech_rate_score(3.0) == pytest.approx(1.0)
    assert speech_rate_score(0.0) == pytest.approx(0.0)
    assert speech_rate_score(6.0) == pytest.approx(0.0)
    assert longest_word_gap([0.0, 2.0, 3.0], [1.0, 2.5, 4.0]) == pytest.approx(1.0)
    assert filler_density("um like you know basically stuff") > 0.3
    assert filler_density("the quick brown fox jumps") == 0.0


def test_window_features_neutral_without_media(tmp_path: Path) -> None:
    ctx = FeatureContext(rms_per_second=[], rms_p95=0.0, scene_times=[], duration=10.0)
    feats = window_features(ctx, ["Hello world."], 3, 0.0, 5.0, "Hello world now.")
    assert feats.audio_energy == pytest.approx(0.5)
    assert feats.question_count == 0
    assert feats.scene_changes == 0
    assert feats.hook_score >= 0.0


def test_rms_per_second_on_tone(tmp_path: Path) -> None:
    wav = make_tone_wav(tmp_path / "tone.wav", seconds=2.0)
    rms = _rms_per_second(wav)
    assert rms is not None
    assert len(rms) == 2
    assert all(v > 0.1 for v in rms)


def test_scene_detection_runs(tmp_path: Path) -> None:
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg")
    video = make_video_with_audio(tmp_path / "v.mp4", seconds=2.0)
    times = _detect_scene_changes(video, Settings())
    assert isinstance(times, list)


# ---------- heuristic scoring ----------


def test_duration_score_length_preference() -> None:
    """No bonus at the minimum, bonus at 30-60 s, penalty above ~75 s."""
    assert duration_score(20.0) == 0.0
    assert duration_score(29.9) == 0.0
    assert duration_score(45.0) == pytest.approx(1.0)
    assert duration_score(60.0) == pytest.approx(1.0)
    assert 0.0 < duration_score(70.0) < 1.0
    assert duration_score(75.0) == pytest.approx(0.5)
    assert duration_score(100.0) < 0.0
    # The term must not overturn a clearly better short window.
    good_short = _features(hook_score=0.9, question_count=2, audio_energy=0.9)
    ok_long = _features(hook_score=0.5, question_count=0, audio_energy=0.5)
    short = heuristic_score(
        good_short,
        boundary_quality=0.9,
        longest_gap=0.3,
        text="Watch this. Did you know why?",
        duration_seconds=25.0,
    )
    long_ = heuristic_score(
        ok_long,
        boundary_quality=0.9,
        longest_gap=0.3,
        text="A decent but plain explanation of the same idea.",
        duration_seconds=50.0,
    )
    assert short > long_


def test_heuristic_score_prefers_good_windows() -> None:
    good = _features(hook_score=0.8, question_count=2, audio_energy=0.9)
    bad = _features(hook_score=0.1, question_count=0, audio_energy=0.2)
    good_score = heuristic_score(
        good, boundary_quality=0.9, longest_gap=0.3, text="Watch this. Did you know why?"
    )
    bad_score = heuristic_score(
        bad, boundary_quality=0.3, longest_gap=4.0, text="um like you know stuff and things"
    )
    assert good_score > bad_score
    assert 0.0 <= bad_score <= 100.0


# ---------- selection ----------


def test_select_clips_overlap_and_gap() -> None:
    cfg = JobOptions(max_clips=8)
    clips = [
        _clip("clip-1", 0.0, 30.0, 90.0),
        _clip("clip-2", 25.0, 55.0, 80.0),  # 5s overlap = 16.7% of shorter -> allowed
        _clip("clip-3", 60.0, 90.0, 70.0),  # 5s gap after clip-2? separation=5 -> ok
        _clip("clip-4", 100.0, 130.0, 60.0),
    ]
    # clip-2 overlaps clip-1: overlap 5s, shorter=30 -> 16.7% <20% allowed,
    # but separation is 0 with overlap>0 -> allowed per rules.
    kept = select_clips(clips, cfg)
    ids = [c.id for c in kept]
    assert "clip-1" in ids
    # clip-3 vs clip-2: separation 5.0 -> allowed
    assert "clip-3" in ids


def test_select_clips_rejects_large_overlap() -> None:
    cfg = JobOptions(max_clips=8)
    clips = [
        _clip("clip-1", 0.0, 40.0, 90.0),
        _clip("clip-2", 10.0, 50.0, 85.0),  # overlap 30/40 = 75% -> rejected
    ]
    kept = select_clips(clips, cfg)
    assert [c.id for c in kept] == ["clip-1"]


def test_select_clips_gap_and_max() -> None:
    cfg = JobOptions(max_clips=2)
    clips = [
        _clip("clip-1", 0.0, 30.0, 90.0),
        _clip("clip-2", 33.0, 60.0, 85.0),  # only 3s gap -> rejected
        _clip("clip-3", 100.0, 130.0, 80.0),
        _clip("clip-4", 200.0, 230.0, 70.0),
    ]
    kept = select_clips(clips, cfg)
    assert [c.id for c in kept] == ["clip-1", "clip-3"]
    chrono = sort_chronologically(list(reversed(kept)))
    assert [c.id for c in chrono] == ["clip-1", "clip-3"]


# ---------- LLM judge ----------


def _judgment_json(cid: str, score: float = 88.0) -> str:
    return json.dumps(
        {
            "clips": [
                {
                    "id": cid,
                    "score": score,
                    "title": "A punchy title",
                    "hook": "The line that grabs attention",
                    "hashtags": ["#one", "#two", "#three"],
                    "rationale": "Strong hook and clear value.",
                }
            ]
        }
    )


def test_parse_judgments_valid_and_fenced() -> None:
    raw = "```json\n" + _judgment_json("candidate-001") + "\n```"
    parsed = _parse_judgments(raw)
    assert "candidate-001" in parsed
    assert isinstance(parsed["candidate-001"], LLMClipJudgment)


def test_parse_judgments_invalid_raises() -> None:
    with pytest.raises(ValueError):
        _parse_judgments("not json at all")
    with pytest.raises(ValueError):
        _parse_judgments(json.dumps({"clips": [{"id": "x"}]}))


class _FakeProvider:
    def __init__(self, responses: list[str], available: bool = True):
        self._responses = list(responses)
        self._available = available
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    def chat(self, system: str, user: str) -> str:
        self.calls += 1
        return self._responses.pop(0)


def test_judge_candidates_success_and_retry() -> None:
    cands = [_candidate("candidate-001"), _candidate("candidate-002", 40.0, 70.0)]
    provider = _FakeProvider(["garbage", _judgment_json("candidate-001")])
    result = judge_candidates(provider, cands)
    assert result is not None
    assert provider.calls == 2
    provider2 = _FakeProvider(["still garbage", "more garbage"])
    assert judge_candidates(provider2, cands) is None
    assert provider2.calls == 2


def test_score_candidates_modes(tmp_path: Path) -> None:
    transcript = _transcript(
        [_segment(0, 0.0, 30.0, "This is a solid candidate window with several sentences.")],
        30.0,
    )
    candidates = [
        _candidate("candidate-001", 0.0, 30.0, 60.0),
        _candidate("candidate-002", 40.0, 70.0, 50.0),
    ]
    # heuristic mode never touches the provider
    provider = _FakeProvider([], available=False)
    clips = score_candidates(
        candidates, transcript, JobOptions(analysis_mode="heuristic"), provider=provider
    )
    assert provider.calls == 0
    assert all(c.source == ClipSource.HEURISTIC for c in clips)

    # llm mode with unreachable provider raises
    with pytest.raises(AnalysisError):
        score_candidates(
            candidates,
            transcript,
            JobOptions(analysis_mode="llm"),
            settings=Settings(llm_base_url="http://127.0.0.1:9", llm_model="x"),
        )

    # llm mode with fake provider blends scores
    judgments = _judgment_json("candidate-001", score=80.0)
    provider_ok = _FakeProvider([judgments])
    clips = score_candidates(
        candidates,
        transcript,
        JobOptions(analysis_mode="llm"),
        provider=provider_ok,
    )
    by_id = {c.id: c for c in clips}
    assert by_id["clip-001"].source == ClipSource.LLM
    assert by_id["clip-001"].score == pytest.approx(0.6 * 80.0 + 0.4 * 60.0)
    assert by_id["clip-002"].source == ClipSource.HEURISTIC

    # auto mode with unreachable provider falls back silently
    clips = score_candidates(
        candidates,
        transcript,
        JobOptions(analysis_mode="auto"),
        settings=Settings(llm_base_url="http://127.0.0.1:9", llm_model="x"),
    )
    assert all(c.source == ClipSource.HEURISTIC for c in clips)


def test_heuristic_metadata_rules() -> None:
    cand = _candidate(text="This is the best setting. Did you know it triples views?")
    title, hook, tags, rationale = _heuristic_metadata(cand, 20.0)
    assert title
    assert len(title) <= 60
    assert hook
    assert 3 <= len(tags) <= 5
    assert "score" in rationale.lower()


# ---------- golden set ----------


def _load_golden(name: str) -> Transcript:
    return Transcript.model_validate_json((GOLDEN_DIR / name).read_text())


def _golden_media() -> MediaInfo:
    return MediaInfo(
        path="/nonexistent/video.mp4",
        duration=200.0,
        width=320,
        height=240,
        fps=30.0,
        has_audio=True,
    )


def test_golden_hooky_beats_rambling() -> None:
    transcript = _load_golden("hooky_vs_rambling.json")
    cfg = JobOptions(min_clip_seconds=20, max_clip_seconds=60, max_clips=4)
    candidates = find_candidates(transcript, _golden_media(), cfg)
    assert candidates
    pairs = [(c.start, c.heuristic_score) for c in candidates]
    # A window inside the hooky region (0-60 s) must outscore rambling ones.
    hooky_best = max(s for st, s in pairs if st < 60)
    ramble_best = max(s for st, s in pairs if st >= 60)
    assert hooky_best > ramble_best


def test_golden_boundaries_and_selection() -> None:
    transcript = _load_golden("hooky_vs_rambling.json")
    cfg = JobOptions(min_clip_seconds=20, max_clip_seconds=60, max_clips=4)
    clips = analyze(transcript, _golden_media(), cfg)
    assert 1 <= len(clips) <= 4
    # Boundaries must land on sentence ends: clip edges coincide with some
    # segment boundary in the source transcript.
    edges = {round(s["end"], 1) for s in transcript.model_dump()["segments"]}
    starts = {round(s["start"], 1) for s in transcript.model_dump()["segments"]}
    for clip in clips:
        assert round(clip.start, 1) in starts or clip.start == pytest.approx(0.0)
        assert round(clip.end, 1) in edges
    # No selected pair may overlap beyond the allowed ratio.
    for i, a in enumerate(clips):
        for b in clips[i + 1 :]:
            overlap = max(0.0, min(a.end, b.end) - max(a.start, b.start))
            shorter = min(a.end - a.start, b.end - b.start)
            assert overlap / shorter < 0.2


def test_golden_short_source_single_candidate() -> None:
    transcript = _load_golden("short_source.json")
    cfg = JobOptions(min_clip_seconds=30, max_clip_seconds=60, max_clips=4)
    candidates = find_candidates(transcript, _golden_media(), cfg)
    assert len(candidates) == 1
    assert candidates[0].start == pytest.approx(0.0)
    assert candidates[0].end == pytest.approx(26.0)
    clips = analyze(transcript, _golden_media(), cfg)
    assert len(clips) == 1
    assert "Short source" in clips[0].rationale
