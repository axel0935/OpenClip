"""LLM judge prompts for highlight ranking."""

import json

from openclip.core.models import Candidate

SYSTEM_PROMPT = (
    "You evaluate short-form video clip candidates (TikTok/Reels/Shorts). "
    "Score 0-100 using: HOOK (35%): does the first 3 seconds make a viewer "
    "stop scrolling — a bold claim, a surprising fact, a question, a tension? "
    "STANDALONE (25%): understandable with no prior context, with a beginning, "
    "a point and a satisfying end. VALUE/EMOTION (25%): teaches something "
    "concrete, tells a story, is funny, controversial or emotionally strong. "
    "PACE (15%): dense, no rambling, no filler. Penalize: mid-sentence starts, "
    "needing prior context, greetings, sponsor reads, housekeeping, repetition. "
    "Be strict: most candidates should score below 70. Titles are punchy, "
    "specific, never clickbait lies. Output ONLY the JSON object."
)


def build_user_prompt(candidates: list[Candidate]) -> str:
    """Serialize candidates for the judge; only text is sent, never audio."""
    payload = [
        {
            "id": c.id,
            "start": round(c.start, 2),
            "end": round(c.end, 2),
            "duration": round(c.end - c.start, 1),
            "text": c.text[:4000],
        }
        for c in candidates
    ]
    return (
        "Score these clip candidates. Return ONLY a JSON object of the form "
        '{"clips":[{"id","score","title","hook","hashtags","rationale"}]} '
        "with one entry per candidate id.\n\n" + json.dumps(payload, ensure_ascii=False)
    )
