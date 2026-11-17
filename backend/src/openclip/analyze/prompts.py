"""LLM judge prompts for highlight ranking."""

import json

from openclip.core.models import Candidate

# Rubric + output rules + two synthetic few-shot examples (no copyrighted
# text). Keep this prompt provider-agnostic: it must work on 2-4B local models.
SYSTEM_PROMPT = """\
You evaluate short-form video clip candidates (TikTok/Reels/Shorts) for a \
local clipping tool. Score each candidate 0-100 using this rubric:
- HOOK (35%): does the first 3 seconds make a viewer stop scrolling — a bold \
claim, a surprising fact, a question, a tension?
- STANDALONE (25%): understandable with no prior context, with a beginning, \
a point and a satisfying end.
- VALUE/EMOTION (25%): teaches something concrete, tells a story, is funny, \
controversial or emotionally strong.
- PACE (15%): dense, no rambling, no filler.
Penalize: mid-sentence starts, needing prior context, greetings, sponsor \
reads, housekeeping, repetition. Be strict: most candidates should score \
below 70; reserve 80+ for genuinely excellent moments.

Output rules (mandatory):
- "title": max 60 characters, punchy and specific, supported by the clip \
text — never a clickbait lie, never a fact that is not in the text.
- No profanity anywhere: titles and hashtags must be clean. If the source \
text is vulgar, use neutral wording or omit the word.
- "hook": a verbatim line copied from the candidate text (the most \
attention-grabbing sentence).
- "hashtags": exactly 3 to 5 lowercase topic words like "#discipline" — \
topic tags only, no profanity, no generic filler like #fyp or #viral.
- "score": integer or float 0-100.
- "rationale": one short sentence justifying the score.
- Output ONLY the JSON object, no prose, no markdown fences.

Example of GOOD output for a candidate about waking up early:
{"clips":[{"id":"c1","score":82,
"title":"Why 4 AM mornings changed everything",
"hook":"The first thing I do every morning is the hardest thing I do all day.",
"hashtags":["#productivity","#morningroutine","#discipline"],
"rationale":"Strong contrarian hook, self-contained story, dense pacing."}]}

Example of BAD output (do NOT do this): title "You won't BELIEVE this!!!", \
hook "trust me bro", hashtags ["#fyp","#viral","#lol"], score 95 for a \
generic greeting — invented hype, hook not from the text, filler tags.
"""


def build_user_prompt(candidates: list[Candidate]) -> str:
    """Serialize candidates for the judge; only text is sent, never audio."""
    payload = [
        {
            "id": c.id,
            "start": round(c.start, 2),
            "end": round(c.end, 2),
            "duration": round(c.end - c.start, 1),
            "text": c.text[:2500],
        }
        for c in candidates
    ]
    return (
        "Score these clip candidates. Return ONLY a JSON object of the form "
        '{"clips":[{"id","score","title","hook","hashtags","rationale"}]} '
        "with one entry per candidate id.\n\n" + json.dumps(payload, ensure_ascii=False)
    )
