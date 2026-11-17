"""Transcript caching keyed by audio content and model parameters."""

import hashlib
import json
from pathlib import Path
from typing import Any

from openclip.core.models import Transcript
from openclip.transcribe.config import TranscribeConfig

CACHE_FILENAME = "transcript.json"


def _sha256_file(path: Path) -> str:
    """Hash a file's content without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(audio_path: Path, resolved_model: str, params: dict[str, Any]) -> str:
    """Return a stable cache key for an audio file plus transcription params."""
    material = {
        "audio_sha256": _sha256_file(audio_path),
        "model": resolved_model,
        "params": params,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def cache_params(cfg: TranscribeConfig) -> dict[str, Any]:
    """Serialize the transcription parameters that affect output."""
    return {
        "language": cfg.language,
        "beam_size": cfg.beam_size,
        "device": cfg.device,
        "compute_type": cfg.compute_type,
        "cpu_threads": cfg.cpu_threads,
        "word_timestamps": True,
        "vad_filter": True,
    }


def _transcript_from_payload(payload: object) -> Transcript | None:
    """Validate the ``transcript`` member of a cache envelope."""
    if not isinstance(payload, dict):
        return None
    try:
        return Transcript.model_validate(payload.get("transcript"))
    except ValueError:
        return None


def load_cached(workdir: Path, key: str) -> Transcript | None:
    """Return the cached transcript when its key matches, else ``None``."""
    cache_file = workdir / CACHE_FILENAME
    if not cache_file.is_file():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("cache_key") != key:
        return None
    return _transcript_from_payload(payload)


def load_cached_transcript(workdir: Path) -> Transcript | None:
    """Return the cached transcript regardless of cache key.

    Used by pipeline resume paths (e.g. clip re-render) that must read the
    ``{"cache_key", "transcript"}`` envelope written by :func:`save_cached`.
    """
    cache_file = workdir / CACHE_FILENAME
    if not cache_file.is_file():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return _transcript_from_payload(payload)


def save_cached(workdir: Path, key: str, transcript: Transcript) -> Path:
    """Persist a transcript together with its cache key."""
    cache_file = workdir / CACHE_FILENAME
    payload = {"cache_key": key, "transcript": transcript.model_dump(mode="json")}
    cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return cache_file
