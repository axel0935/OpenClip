"""Captions stage: turn a transcript plus a style preset into an ASS document.

The pipeline's captions contract is :func:`build_ass`. The render stage (Step 7)
needs :func:`ffmpeg_subtitles_filter` to burn the result in.
"""

from openclip.captions.ass import build_ass
from openclip.captions.fonts import ffmpeg_subtitles_filter, font_path, fonts_dir
from openclip.captions.presets import load_presets

__all__ = [
    "build_ass",
    "ffmpeg_subtitles_filter",
    "font_path",
    "fonts_dir",
    "load_presets",
]
