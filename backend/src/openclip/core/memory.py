"""Cheap host memory inspection for the local resource guard.

OpenClip must never load an optional model it cannot afford: on the reference
N305 machine a 2B LLM alongside Whisper exhausted RAM and turned a 70 second
job into a 10 minute one. This module reads the kernel's own estimate of
reclaimable memory and fails soft everywhere (unknown, unreadable, non-Linux),
so callers treat "unknown" as "do not restrict".
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

MEMINFO_PATH = Path("/proc/meminfo")


def available_memory_bytes(meminfo: Path = MEMINFO_PATH) -> int | None:
    """Return the kernel's ``MemAvailable`` in bytes, or ``None`` if unknown.

    ``None`` means the value could not be determined (not Linux, unreadable
    file, missing or malformed ``MemAvailable`` line). Callers must treat it as
    "no evidence of pressure" rather than as zero.
    """
    try:
        text = meminfo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("MemAvailable:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            return int(parts[1]) * 1024
        except ValueError:
            return None
    return None


def has_free_memory(min_bytes: int, meminfo: Path = MEMINFO_PATH) -> bool:
    """True when at least ``min_bytes`` are available, or when it is unknown."""
    available = available_memory_bytes(meminfo)
    if available is None:
        log.debug("MemAvailable unavailable; not restricting optional work")
        return True
    return available >= min_bytes
