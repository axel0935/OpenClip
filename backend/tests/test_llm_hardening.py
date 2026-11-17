"""Tests for the hardened LLM judge layer (MockTransport, no real model)."""

import json
import threading
from typing import Any

import httpx
import pytest

from openclip.analyze.llm import (
    RECOMMENDED_LLM_MODELS,
    LLMClipJudgment,
    OpenAICompatibleProvider,
    _pick_recommended,
    build_provider,
    judge_candidates,
)
from openclip.analyze.service import _clean_judgment, _heuristic_metadata, _mask_profanity
from openclip.core.config import Settings
from openclip.core.errors import AnalysisError
from openclip.core.models import Candidate, CandidateFeatures


def _candidate(cid: str, start: float, text: str, score: float = 50.0) -> Candidate:
    return Candidate(
        id=cid,
        start=start,
        end=start + 30.0,
        text=text,
        features=CandidateFeatures(
            speech_rate_wps=3.0,
            audio_energy=0.5,
            question_count=0,
            scene_changes=0,
            hook_score=0.5,
        ),
        heuristic_score=score,
    )


def _judgment(cid: str, **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": cid,
        "score": 80.0,
        "title": "A solid title",
        "hook": "A hook line",
        "hashtags": ["#one", "#two", "#three"],
        "rationale": "Because reasons.",
    }
    data.update(over)
    return data


def _ok_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
        },
    )


class _Recorder:
    """MockTransport handler recording requests and replying per call."""

    def __init__(self, replies: list[httpx.Response]):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.replies.pop(0) if self.replies else httpx.Response(500)


def _provider(handler: _Recorder, **kw: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        "http://test.local/v1", "test-model", transport=httpx.MockTransport(handler), **kw
    )


TEXT = "Discipline is doing what needs to be done. Most people quit too early."


def test_chat_request_shape_json_mode() -> None:
    handler = _Recorder([_ok_response({"clips": []})])
    provider = _provider(handler)
    provider.chat("sys", "user")
    body = json.loads(handler.requests[0].content)
    assert body["model"] == "test-model"
    assert body["stream"] is False
    assert body["temperature"] == 0.2
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"


def test_chat_falls_back_without_json_mode_on_400() -> None:
    handler = _Recorder(
        [
            httpx.Response(400, json={"error": "response_format unsupported"}),
            _ok_response({"clips": []}),
        ]
    )
    provider = _provider(handler)
    assert provider.chat("sys", "user") != ""
    assert len(handler.requests) == 2
    second = json.loads(handler.requests[1].content)
    assert "response_format" not in second


def test_chat_retries_once_on_transport_error() -> None:
    handler = _Recorder(
        [
            httpx.Response(503),
            _ok_response({"clips": []}),
        ]
    )
    provider = _provider(handler)
    provider.chat("sys", "user")
    assert len(handler.requests) == 2


def test_chat_raises_after_retries_exhausted() -> None:
    handler = _Recorder([httpx.Response(500), httpx.Response(500)])
    provider = _provider(handler)
    with pytest.raises(AnalysisError):
        provider.chat("sys", "user")


def test_unload_posts_keep_alive_zero() -> None:
    handler = _Recorder([httpx.Response(200, json={})])
    provider = _provider(handler)
    provider.unload()
    req = handler.requests[0]
    assert req.url.path == "/api/generate"
    body = json.loads(req.content)
    assert body["model"] == "test-model"
    assert body["keep_alive"] == 0


def test_judge_batches_candidates() -> None:
    cands = [_candidate(f"candidate-{i:03d}", i * 40.0, TEXT) for i in range(12)]
    calls = []

    class BatchProvider:
        def is_available(self) -> bool:
            return True

        def chat(self, system: str, user: str) -> str:
            calls.append(user)
            payload = json.loads(user[user.index("\n\n") + 2 :])
            return json.dumps(
                {
                    "clips": [
                        _judgment(c["id"], hook="Discipline is doing what needs") for c in payload
                    ]
                }
            )

    judgments = judge_candidates(BatchProvider(), cands, batch_size=5)
    sizes = [json.loads(u[u.index("\n\n") + 2 :]).__len__() for u in calls]
    assert judgments is not None and len(judgments) == 12
    assert len(calls) == 3  # 12 candidates / batch of 5
    assert sizes == [5, 5, 2]


def test_judge_partial_batch_failure_falls_back_per_candidate() -> None:
    cands = [_candidate(f"candidate-{i:03d}", i * 40.0, TEXT) for i in range(10)]

    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def chat(self, system: str, user: str) -> str:
            self.calls += 1
            if self.calls <= 2:  # first batch fails twice
                return "garbage"
            payload = json.loads(user[user.index("\n\n") + 2 :])
            return json.dumps({"clips": [_judgment(c["id"]) for c in payload]})

    judgments = judge_candidates(FlakyProvider(), cands, batch_size=5)
    assert judgments is not None
    assert len(judgments) == 5  # only the second batch judged


def test_judge_cancellation_between_batches() -> None:
    cands = [_candidate(f"candidate-{i:03d}", i * 40.0, TEXT) for i in range(10)]
    cancel = threading.Event()

    class CancelProvider:
        def is_available(self) -> bool:
            return True

        def chat(self, system: str, user: str) -> str:
            cancel.set()
            payload = json.loads(user[user.index("\n\n") + 2 :])
            return json.dumps({"clips": [_judgment(c["id"]) for c in payload]})

    with pytest.raises(AnalysisError):
        judge_candidates(CancelProvider(), cands, batch_size=5, cancel=cancel)


def test_build_provider_auto_detects_recommended_model() -> None:
    models = {"data": [{"id": m} for m in ["qwen2.5:3b", "gemma2:2b", "other:1b"]]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=models)
        return httpx.Response(200, json={"choices": []})

    transport = httpx.MockTransport(handler)
    provider = OpenAICompatibleProvider("http://test.local/v1", "test-model", transport=transport)
    installed = provider.list_models()
    assert _pick_recommended(installed) == "gemma2:2b"  # first recommended installed
    assert RECOMMENDED_LLM_MODELS[0] == "gemma2:2b"


def test_build_provider_returns_none_when_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    # build_provider constructs its own client; point it at a dead port.
    settings = Settings(llm_base_url="http://127.0.0.1:9/v1", llm_model=None)
    assert build_provider(settings) is None


# ---------- cleaning rules ----------


def test_mask_profanity() -> None:
    assert _mask_profanity("I don't need fucking purpose.") == "I don't need f*** purpose."
    assert _mask_profanity("clean text") == "clean text"


def test_clean_judgment_masks_and_repairs() -> None:
    cand = _candidate(
        "candidate-001",
        0.0,
        "Discipline is doing what needs to be done. Most people quit too early.",
    )
    judgment = LLMClipJudgment.model_validate(
        _judgment(
            "candidate-001",
            title="Discipline is fucking hard",
            hook="This hook was never said in the clip",
            hashtags=["#discipline", "#fucking", "#fyp", "#motivation"],
            score=250.0,
        )
    )
    title, hook, tags, rationale, score = _clean_judgment(judgment, cand)
    assert "f***" in title and "fucking" not in title
    assert "fucking" not in hook  # replaced by a verbatim line
    assert hook in cand.text or "***" in hook
    assert "#fucking" not in tags and "#fyp" not in tags
    assert 3 <= len(tags) <= 5
    assert score == 100.0  # clamped


def test_clean_judgment_rejects_unsupported_title() -> None:
    cand = _candidate(
        "candidate-001",
        0.0,
        "The quarterly earnings report showed steady growth across all regions.",
    )
    judgment = LLMClipJudgment.model_validate(
        _judgment(
            "candidate-001",
            title="Celebrity scandal rocks Hollywood",
            hook="The quarterly earnings report showed steady growth",
        )
    )
    title, hook, tags, rationale, score = _clean_judgment(judgment, cand)
    assert "earnings" in title.lower() or "growth" in title.lower()
    assert "scandal" not in title.lower()


def test_heuristic_metadata_cleans_profanity() -> None:
    cand = _candidate(
        "candidate-001",
        0.0,
        "I don't need fucking purpose. You already have it inside you.",
        score=40.0,
    )
    title, hook, tags, rationale = _heuristic_metadata(cand, 20.0)
    assert "fucking" not in title
    assert all("fuck" not in t for t in tags)


# ---------- slow: real Ollama model ----------


@pytest.mark.slow
def test_real_ollama_judges_synthetic_candidates() -> None:
    """End-to-end judge run against the installed recommended model."""
    import shutil

    if shutil.which("ollama") is None:
        pytest.skip("ollama binary not installed")
    provider = build_provider(Settings())
    if provider is None:
        pytest.skip("no recommended Ollama model installed")
    try:
        cands = [
            _candidate("candidate-001", 0.0, TEXT),
            _candidate("candidate-002", 40.0, "Water the plants every morning."),
            _candidate("candidate-003", 80.0, "The secret is showing up when it is hard."),
        ]
        judgments = judge_candidates(provider, cands, batch_size=3)
        assert judgments is not None and len(judgments) == 3
        for j in judgments.values():
            assert 0.0 <= j.score <= 100.0
            assert j.title and len(j.title) <= 60
            assert 3 <= len(j.hashtags) <= 5
    finally:
        provider.close()
