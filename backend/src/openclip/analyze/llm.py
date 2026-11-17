"""Pluggable LLM judge for highlight ranking (OpenAI-compatible API)."""

import json
import logging
import os
import re
import threading
from collections.abc import Callable
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from openclip.analyze.prompts import SYSTEM_PROMPT, build_user_prompt
from openclip.core.errors import AnalysisError
from openclip.core.models import Candidate

log = logging.getLogger(__name__)

_PROBE_TIMEOUT = 2.0
_DEFAULT_TIMEOUT = 180.0
_MAX_ATTEMPTS = 2  # one retry
_UNLOAD_TIMEOUT = 5.0
_OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434/v1"
_DEFAULT_BATCH_SIZE = 5

#: Local models recommended for the judge, best quality-per-resource first.
#: ``OPENCLIP_LLM_MODEL`` or an explicit ``llm_model`` setting overrides this.
RECOMMENDED_LLM_MODELS: tuple[str, ...] = (
    "gemma2:2b",
    "qwen2.5:3b",
    "llama3.2:3b",
    "phi3.5",
)

ProgressCallback = Callable[[float, str], None]


def _no_op(_fraction: float, _message: str) -> None:
    """Default progress sink."""


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
        timeout: float = _DEFAULT_TIMEOUT,
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
        self._json_mode = True

    @property
    def model(self) -> str:
        """Return the configured model id."""
        return self._model

    def is_available(self) -> bool:
        """Probe ``GET /models`` with a short timeout."""
        try:
            resp = self._client.get("/models", timeout=_PROBE_TIMEOUT)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[str]:
        """Return installed model ids, or an empty list on failure."""
        try:
            resp = self._client.get("/models", timeout=_PROBE_TIMEOUT)
            if resp.status_code != 200:
                return []
            data = resp.json().get("data")
        except (httpx.HTTPError, ValueError, AttributeError):
            return []
        if not isinstance(data, list):
            return []
        return [str(item["id"]) for item in data if isinstance(item, dict) and item.get("id")]

    def _payload(self, system: str, user: str) -> dict:
        payload: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def chat(self, system: str, user: str) -> str:
        """POST one chat completion; one retry on transport or HTTP errors.

        Endpoints that reject ``response_format`` get one fallback attempt
        without JSON mode before the error is raised.
        """
        last_error: Exception | None = None
        for _ in range(_MAX_ATTEMPTS):
            try:
                resp = self._client.post("/chat/completions", json=self._payload(system, user))
                if resp.status_code == 400 and self._json_mode:
                    self._json_mode = False
                    log.info("endpoint rejected JSON mode; retrying without it")
                    continue
                resp.raise_for_status()
                data = resp.json()
                return str(data["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
        raise AnalysisError(f"LLM request failed: {last_error}")

    def unload(self) -> None:
        """Best-effort model unload (Ollama ``keep_alive: 0``).

        Derives the native API root by stripping a trailing ``/v1``; on
        non-Ollama endpoints the call 404s and is ignored.
        """
        root = self._client.base_url
        base = str(root).rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            self._client.post(
                f"{base}/api/generate",
                json={"model": self._model, "keep_alive": 0},
                timeout=_UNLOAD_TIMEOUT,
            )
        except httpx.HTTPError:
            pass

    def close(self) -> None:
        """Release the underlying HTTP client."""
        self._client.close()


def _pick_recommended(installed: list[str]) -> str | None:
    """Return the best recommended model present in ``installed``."""
    for rec in RECOMMENDED_LLM_MODELS:
        short = rec.split(":")[0]
        if rec in installed:
            return rec
        # Accept size-tagged variants, e.g. installed "qwen2.5:3b-instruct".
        for name in installed:
            if name.split(":")[0] == short:
                return name
    return None


def build_provider(settings) -> OpenAICompatibleProvider | None:
    """Create a provider from settings, or ``None`` when unavailable.

    With no explicit ``llm_base_url``/``llm_model`` the Ollama default
    endpoint is probed; the best installed recommended model is picked.
    """
    base_url = getattr(settings, "llm_base_url", None) or _OLLAMA_DEFAULT_URL
    model = getattr(settings, "llm_model", None)
    provider = OpenAICompatibleProvider(
        base_url,
        model or "",
        api_key=os.environ.get("OPENCLIP_LLM_API_KEY"),
        timeout=float(getattr(settings, "llm_timeout_seconds", _DEFAULT_TIMEOUT)),
    )
    if not provider.is_available():
        if getattr(settings, "llm_base_url", None):
            log.info("configured LLM endpoint %s is unreachable", base_url)
        return None
    if not model:
        installed = provider.list_models()
        model = _pick_recommended(installed)
        if model is None:
            log.info(
                "no recommended LLM model installed at %s (have: %s); using heuristic scoring",
                base_url,
                ", ".join(installed) or "none",
            )
            provider.close()
            return None
        provider._model = model
        log.info("auto-selected LLM model %r from %s", model, base_url)
    return provider


class LLMClipJudgment(BaseModel):
    """One validated clip judgment returned by the LLM judge."""

    id: str = Field(min_length=1)
    score: float
    title: str = Field(min_length=1, max_length=60)
    hook: str = Field(min_length=1, max_length=160)
    hashtags: list[str] = Field(min_length=3, max_length=5)
    rationale: str = Field(min_length=1, max_length=200)


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


def _clip_field(item: dict, key: str, limit: int, default: str = "") -> str:
    """Coerce a judgment field to a single-line string within ``limit`` chars."""
    value = item.get(key)
    if not isinstance(value, str):
        return default
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut.rstrip(",;:") or text[:limit]).strip()


def _sanitize_clip(item: object) -> LLMClipJudgment | None:
    """Repair one raw clip object; return ``None`` when unusable.

    Small local models routinely overshoot field limits (long hooks, odd
    hashtag shapes). Clamping here keeps one bad field from rejecting the
    whole batch; entries without a usable id are dropped.
    """
    if not isinstance(item, dict):
        return None
    cid = item.get("id")
    if not isinstance(cid, str) or not cid.strip():
        return None
    try:
        score = float(item.get("score"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        score = 50.0
    score = max(0.0, min(100.0, score))
    title = _clip_field(item, "title", 60)
    if not title:
        return None
    hook = _clip_field(item, "hook", 160) or title
    raw_tags = item.get("hashtags")
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, str) and tag.strip():
                word = tag.strip().lstrip("#")
                tags.append(f"#{word}" if word else "#openclip")
    tags = tags[:5]
    while len(tags) < 3:
        tags.append("#openclip")
    rationale = _clip_field(item, "rationale", 200) or "LLM judgment."
    try:
        return LLMClipJudgment(
            id=cid.strip(),
            score=score,
            title=title,
            hook=hook,
            hashtags=tags,
            rationale=rationale,
        )
    except ValidationError:
        return None


def _parse_judgments(raw: str) -> dict[str, LLMClipJudgment]:
    """Parse and validate judge output; raise on malformed JSON.

    Individual clip entries are sanitized before validation so one oversized
    field does not reject the whole batch. Raises when the envelope is not
    JSON or contains no usable clip at all.
    """
    payload = _strip_fences(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        # Tolerate leading/trailing prose around the JSON object.
        match = re.search(r"\{.*\}", payload, re.DOTALL)
        if not match:
            raise ValueError("no JSON object in judge response") from exc
        data = json.loads(match.group(0))
    clips = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(clips, list):
        raise ValueError("judge response has no 'clips' list")
    judgments = {
        clip.id: clip for clip in (_sanitize_clip(item) for item in clips) if clip is not None
    }
    if not judgments:
        raise ValueError("judge response contained no usable clip judgments")
    return judgments


def _batches(candidates: list[Candidate], size: int) -> list[list[Candidate]]:
    """Split candidates into fixed-size batches for bounded prompts."""
    size = max(1, size)
    return [candidates[i : i + size] for i in range(0, len(candidates), size)]


def judge_candidates(
    provider: LLMProvider,
    candidates: list[Candidate],
    *,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> dict[str, LLMClipJudgment] | None:
    """Ask the judge to score candidates; ``None`` on unrecoverable failure.

    Candidates are judged in batches so small local models stay inside their
    context window. A batch that fails twice is skipped (its candidates fall
    back to heuristic metadata); ``None`` is returned only when every batch
    fails. Cancellation between batches raises :class:`AnalysisError`.
    """
    if not candidates:
        return {}
    batches = _batches(candidates, batch_size)
    judgments: dict[str, LLMClipJudgment] = {}
    failures = 0
    report = progress or _no_op
    for index, batch in enumerate(batches):
        if cancel is not None and cancel.is_set():
            raise AnalysisError("Analysis cancelled.")
        user = build_user_prompt(batch)
        batch_judgments: dict[str, LLMClipJudgment] | None = None
        last_error: Exception | None = None
        for _ in range(_MAX_ATTEMPTS):
            try:
                raw = provider.chat(SYSTEM_PROMPT, user)
                batch_judgments = _parse_judgments(raw)
                break
            except (AnalysisError, ValueError, ValidationError) as exc:
                last_error = exc
                log.info("LLM judge batch %d attempt failed: %s", index + 1, exc)
        if batch_judgments is None:
            failures += 1
            log.warning(
                "LLM judge batch %d/%d failed; heuristic fallback: %s",
                index + 1,
                len(batches),
                last_error,
            )
        else:
            judgments.update(batch_judgments)
        report(
            (index + 1) / len(batches),
            f"Judging candidates ({index + 1}/{len(batches)})",
        )
    if failures == len(batches):
        log.warning("LLM judge failed on every batch; falling back to heuristic scores")
        return None
    return judgments
