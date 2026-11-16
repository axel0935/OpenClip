"""Pluggable LLM judge for highlight ranking (OpenAI-compatible API)."""

import json
import logging
import os
import re
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from openclip.analyze.prompts import SYSTEM_PROMPT, build_user_prompt
from openclip.core.errors import AnalysisError
from openclip.core.models import Candidate

log = logging.getLogger(__name__)

_PROBE_TIMEOUT = 2.0
_REQUEST_TIMEOUT = 60.0
_MAX_RETRIES = 2


class LLMProvider(Protocol):
    """Minimal chat-completion interface used by the judge."""

    def is_available(self) -> bool:
        """Return True when the endpoint answers a quick probe."""
        ...

    def chat(self, system: str, user: str) -> str:
        """Return the assistant message content for one exchange."""
        ...


class OpenAICompatibleProvider:
    """Chat provider for Ollama ``/v1`` or any OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = _REQUEST_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )
        self._model = model

    def is_available(self) -> bool:
        """Probe ``GET /models`` with a short timeout."""
        try:
            resp = self._client.get("/models", timeout=_PROBE_TIMEOUT)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def chat(self, system: str, user: str) -> str:
        """POST one chat completion with a single retry on transport errors."""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        last_error: Exception | None = None
        for _ in range(_MAX_RETRIES):
            try:
                resp = self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return str(data["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
        raise AnalysisError(f"LLM request failed: {last_error}")

    def close(self) -> None:
        """Release the underlying HTTP client."""
        self._client.close()


def build_provider(settings) -> OpenAICompatibleProvider | None:
    """Create a provider from settings, or ``None`` when not configured."""
    base_url = getattr(settings, "llm_base_url", None)
    model = getattr(settings, "llm_model", None)
    if not base_url or not model:
        return None
    return OpenAICompatibleProvider(
        base_url,
        model,
        api_key=os.environ.get("OPENCLIP_LLM_API_KEY"),
    )


class LLMClipJudgment(BaseModel):
    """One validated clip judgment returned by the LLM judge."""

    id: str = Field(min_length=1)
    score: float
    title: str = Field(min_length=1, max_length=60)
    hook: str = Field(min_length=1, max_length=160)
    hashtags: list[str] = Field(min_length=3, max_length=5)
    rationale: str = Field(min_length=1, max_length=200)


class LLMJudgeResponse(BaseModel):
    """Strict JSON envelope expected from the judge."""

    clips: list[LLMClipJudgment]


def _strip_fences(text: str) -> str:
    """Remove markdown code fences around a JSON payload."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _parse_judgments(raw: str) -> dict[str, LLMClipJudgment]:
    """Parse and validate judge output; raise on malformed JSON."""
    payload = _strip_fences(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        # Tolerate leading/trailing prose around the JSON object.
        match = re.search(r"\{.*\}", payload, re.DOTALL)
        if not match:
            raise ValueError("no JSON object in judge response") from exc
        data = json.loads(match.group(0))
    response = LLMJudgeResponse.model_validate(data)
    return {clip.id: clip for clip in response.clips}


def judge_candidates(
    provider: LLMProvider,
    candidates: list[Candidate],
) -> dict[str, LLMClipJudgment] | None:
    """Ask the judge to score candidates; ``None`` on unrecoverable failure."""
    if not candidates:
        return {}
    user = build_user_prompt(candidates)
    last_error: Exception | None = None
    for _ in range(2):
        try:
            raw = provider.chat(SYSTEM_PROMPT, user)
            judgments = _parse_judgments(raw)
            return judgments
        except (AnalysisError, ValueError, ValidationError) as exc:
            last_error = exc
            log.info("LLM judge attempt failed, retrying once: %s", exc)
    log.warning("LLM judge failed twice; falling back to heuristic scores: %s", last_error)
    return None
