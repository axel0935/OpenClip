"""Helpers to generate tiny royalty-free media fixtures with FFmpeg."""

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest


def require_ffmpeg() -> None:
    """Skip the calling test when FFmpeg tooling is unavailable."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()}")


def make_video_with_audio(path: Path, seconds: float = 3.0) -> Path:
    """Generate a tiny test video with a sine-tone audio track."""
    require_ffmpeg()
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=320x240:rate=15",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    return path


def make_video_no_audio(path: Path, seconds: float = 2.0) -> Path:
    """Generate a tiny test video without an audio track."""
    require_ffmpeg()
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=320x240:rate=15",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ]
    )
    return path


def make_corrupt_media(path: Path) -> Path:
    """Write bytes that are not a valid media container."""
    path.write_bytes(b"this is not a media file\x00\x01\x02" * 64)
    return path


def make_speech_wav(path: Path, seconds: float = 4.0) -> Path:
    """Synthesize a speech-like WAV using FFmpeg's flite source if available."""
    require_ffmpeg()
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False
    )
    if "flite" not in proc.stdout:
        pytest.skip("ffmpeg flite filter not available")
    text = (
        "Hello and welcome to OpenClip. "
        "This is a short synthetic speech sample for transcription tests. "
        "The quick brown fox jumps over the lazy dog."
    )
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"flite=text='{text}':voice=slt",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(path),
        ]
    )
    return path


def make_tone_wav(path: Path, seconds: float = 2.0, freq: float = 440.0) -> Path:
    """Write a 16 kHz mono sine WAV without invoking FFmpeg."""
    rate = 16000
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        for i in range(frames):
            sample = int(12000 * math.sin(2 * math.pi * freq * i / rate))
            wav.writeframesraw(struct.pack("<h", sample))
    return path
