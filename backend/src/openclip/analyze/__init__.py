"""Highlight detection: candidates, scoring, LLM judge and selection."""

from openclip.analyze.candidates import find_candidates
from openclip.analyze.llm import LLMProvider, OpenAICompatibleProvider, build_provider
from openclip.analyze.select import select_clips, sort_chronologically
from openclip.analyze.service import analyze, score_candidates

__all__ = [
    "LLMProvider",
    "OpenAICompatibleProvider",
    "analyze",
    "build_provider",
    "find_candidates",
    "score_candidates",
    "select_clips",
    "sort_chronologically",
]
