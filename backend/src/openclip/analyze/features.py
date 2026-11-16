"""Objective per-window features for highlight candidates."""

import logging
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from openclip.core.config import Settings
from openclip.core.models import CandidateFeatures, MediaInfo, Transcript

log = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
# ffmpeg scene detection runs on a tiny proxy stream: 2 fps is enough for
# shot-boundary counting and keeps CPU usage low on the N305.
_SCENE_FPS = 2
_SCENE_THRESHOLD = 0.3
_SCENE_WIDTH = 320
_SCENE_TIMEOUT_SECONDS = 600


@dataclass
class FeatureContext:
    """Precomputed signals shared by every candidate window."""

    rms_per_second: list[float]
    rms_p95: float
    scene_times: list[float]
    duration: float

    def energy(self, start: float, end: float) -> float:
        """Mean percentile-normalized RMS energy inside ``[start, end]``."""
        if not self.rms_per_second or self.rms_p95 <= 0:
            return 0.5
        lo = max(0, int(start))
        hi = min(len(self.rms_per_second), max(lo + 1, int(end) + 1))
        if hi <= lo:
            return 0.5
        values = [min(1.0, v / self.rms_p95) for v in self.rms_per_second[lo:hi]]
        return sum(values) / len(values)

    def scene_changes(self, start: float, end: float) -> int:
        """Count detected scene cuts inside ``[start, end]``."""
        return sum(1 for t in self.scene_times if start <= t <= end)


def _rms_per_second(audio_path: Path) -> list[float] | None:
    """Compute per-second RMS of a 16 kHz mono WAV without loading it whole."""
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import audioop  # noqa: PLC0415 - deprecated, removed in Python 3.13
    except ImportError:
        audioop = None
    if audioop is None:
        return _rms_per_second_numpy(audio_path)
    try:
        with wave.open(str(audio_path), "rb") as wav:
            if wav.getframerate() != _SAMPLE_RATE or wav.getnchannels() != 1:
                log.warning("audio.wav is not 16 kHz mono; skipping energy feature")
                return None
            if wav.getsampwidth() != 2:
                return None
            out: list[float] = []
            while True:
                frames = wav.readframes(_SAMPLE_RATE)
                if not frames:
                    break
                out.append(audioop.rms(frames, 2) / 32768.0)
            return out
    except (wave.Error, EOFError, OSError) as exc:
        log.warning("could not read audio.wav for energy feature: %s", exc)
        return None


def _rms_per_second_numpy(audio_path: Path) -> list[float] | None:
    """Fallback RMS computation using numpy when audioop is unavailable."""
    try:
        import numpy as np  # noqa: PLC0415 - optional transitive dependency
    except ImportError:
        log.warning("no RMS implementation available; using neutral audio energy")
        return None
    try:
        raw = np.fromfile(audio_path, dtype=np.int16)
    except OSError as exc:
        log.warning("could not read audio.wav for energy feature: %s", exc)
        return None
    seconds = len(raw) // _SAMPLE_RATE
    out: list[float] = []
    for i in range(seconds):
        chunk = raw[i * _SAMPLE_RATE : (i + 1) * _SAMPLE_RATE].astype(np.float64)
        out.append(float(np.sqrt(np.mean(chunk * chunk))) / 32768.0)
    return out


def _percentile(values: list[float], q: float) -> float:
    """Return the ``q`` percentile (0-100) of ``values``."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q / 100 * (len(ordered) - 1))))
    return ordered[idx]


def _detect_scene_changes(media_path: Path, settings: Settings) -> list[float]:
    """Return timestamps of shot boundaries via ffmpeg scene detection."""
    if not media_path.is_file():
        return []
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(media_path),
        "-vf",
        f"fps={_SCENE_FPS},scale={_SCENE_WIDTH}:-1,select='gt(scene,{_SCENE_THRESHOLD})',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_SCENE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("scene detection failed: %s", exc)
        return []
    times: list[float] = []
    for line in proc.stderr.splitlines():
        marker = "pts_time:"
        if marker in line:
            try:
                times.append(float(line.split(marker, 1)[1].split()[0]))
            except (ValueError, IndexError):
                continue
    return times


def build_context(transcript: Transcript, media: MediaInfo, settings: Settings) -> FeatureContext:
    """Build the shared feature context for one transcript/media pair."""
    workdir = Path(media.path).parent
    audio_path = workdir / "audio.wav"
    rms = _rms_per_second(audio_path) if audio_path.is_file() else None
    rms = rms or []
    scene_times = _detect_scene_changes(Path(media.path), settings)
    return FeatureContext(
        rms_per_second=rms,
        rms_p95=_percentile(rms, 95),
        scene_times=scene_times,
        duration=transcript.duration or media.duration,
    )


_QUESTION_STARTERS = {
    "who",
    "what",
    "when",
    "where",
    "why",
    "how",
    "is",
    "are",
    "was",
    "were",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "will",
}


def is_question(text: str) -> bool:
    """Return True when a sentence reads as a question."""
    stripped = text.strip()
    if not stripped:
        return False
    if "?" in stripped:
        return True
    first = stripped.split(None, 1)[0].strip("\"'(").lower().rstrip(",")
    return first in _QUESTION_STARTERS


_STRONG_CLAIM_TERMS = {
    "never",
    "nobody",
    "nothing",
    "secret",
    "truth",
    "mistake",
    "warning",
    "shocking",
    "insane",
    "crazy",
    "finally",
    "actually",
    "literally",
    "stop",
    "wrong",
    "myth",
    "lie",
    "lies",
    "scam",
    "hack",
    "proven",
    "science",
    "why",
    "how",
}

_FILLER_OPENERS = {"so", "um", "uh", "yeah", "yep", "like", "well", "basically", "actually"}
_CONTRAST_TERMS = {"but", "however", "instead", "although", "though", "yet", "except"}
_DIGIT_HINTS = {"percent", "x", "times", "million", "billion", "thousand", "half", "double"}


def hook_score(text: str) -> float:
    """Score how gripping the opening of a window is, from 0 to 1.

    Rewards questions, numbers, strong claims, second-person address and
    contrast; penalizes filler openers, pronoun-led narration, conjunction
    starts and greetings. Operates on roughly the first 3 seconds of text.
    """
    import re  # noqa: PLC0415 - local import keeps module load light

    words = re.findall(r"[a-zA-Z']+|\d+", text.lower())
    if not words:
        return 0.0
    score = 0.0
    first = words[0]
    first_three = " ".join(words[:12])

    if "?" in text or first in _QUESTION_STARTERS:
        score += 0.30
    if any(w.isdigit() or w in _DIGIT_HINTS for w in words[:15]):
        score += 0.20
    if any(w in _STRONG_CLAIM_TERMS for w in words[:20]):
        score += 0.25
    if re.search(r"\b(you|your|yours|you're)\b", first_three):
        score += 0.15
    if any(w in _CONTRAST_TERMS for w in words[:15]):
        score += 0.15

    if first in _FILLER_OPENERS or first_three.startswith(("so um", "you know", "i mean")):
        score -= 0.30
    if re.match(r"^(he|she|it|they|we)\s+(said|says|was|were|is|has|have|had)\b", first_three):
        score -= 0.25
    if first in {"and", "but", "so", "because", "which", "that"}:
        score -= 0.15
    if first in {"hi", "hello", "hey", "welcome", "guys"}:
        score -= 0.20
    return max(0.0, min(1.0, score))


def text_within(text_parts: list[tuple[float, float, str]], start: float, end: float) -> str:
    """Join the pieces of ``text_parts`` overlapping ``[start, end]``."""
    return " ".join(t for s, e, t in text_parts if e > start and s < end).strip()


def window_features(
    ctx: FeatureContext,
    texts: list[str],
    word_count: int,
    start: float,
    end: float,
    hook_text: str,
) -> CandidateFeatures:
    """Compute the contract :class:`CandidateFeatures` for one window."""
    duration = max(0.1, end - start)
    speech_rate = word_count / duration
    questions = sum(1 for t in texts if is_question(t))
    return CandidateFeatures(
        speech_rate_wps=round(speech_rate, 3),
        audio_energy=round(ctx.energy(start, end), 4),
        question_count=questions,
        scene_changes=ctx.scene_changes(start, end),
        hook_score=round(hook_score(hook_text), 4),
    )


def ffmpeg_available(settings: Settings) -> bool:
    """Return True when the configured ffmpeg binary exists."""
    return shutil.which(settings.ffmpeg_path) is not None
