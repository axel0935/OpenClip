"""Shared test fixtures for OpenClip backend tests."""

import importlib.util
import os

# Keep the suite hermetic: never let the LLM judge reach a real endpoint
# (e.g. a local Ollama) from unit tests. Tests that exercise the judge pass
# an explicit provider or settings instead.
os.environ.setdefault("OPENCLIP_LLM_BASE_URL", "http://127.0.0.1:9")
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# The reframe stage requires the optional 'vision' extra (numpy/opencv).
if importlib.util.find_spec("numpy") is None:
    collect_ignore = ["test_reframe.py"]
