"""Shared test fixtures for OpenClip backend tests."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# The reframe stage requires the optional 'vision' extra (numpy/opencv).
if importlib.util.find_spec("numpy") is None:
    collect_ignore = ["test_reframe.py"]
