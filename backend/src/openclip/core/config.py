"""Application configuration loaded from the local environment."""

from pathlib import Path
from typing import Literal

from platformdirs import user_data_path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Local OpenClip settings, overridable with ``OPENCLIP_`` variables."""

    model_config = SettingsConfigDict(env_prefix="OPENCLIP_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = user_data_path("openclip", appauthor=False)
    whisper_profile: Literal["auto", "fast", "balanced", "quality"] = "auto"
    analysis_mode: Literal["auto", "heuristic", "llm"] = "auto"
    llm_base_url: str | None = None
    llm_model: str | None = None
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    max_video_minutes: int = 240
    max_download_mb: int = 4096
