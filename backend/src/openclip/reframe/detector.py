"""Face detection with the YuNet model (OpenCV Zoo, MIT license)."""

import hashlib
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np  # pyright: ignore[reportMissingImports] - vision extra

from openclip.core.config import Settings
from openclip.reframe.errors import ReframeError

log = logging.getLogger(__name__)

MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"
MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)
# SHA-256 of the verified 2023mar YuNet release (232,589 bytes).
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
_DOWNLOAD_TIMEOUT = 60


@dataclass(frozen=True)
class Face:
    """A detected face in frame pixels."""

    x: float
    y: float
    w: float
    h: float
    confidence: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h


class FaceDetector(Protocol):
    """Interface for face detectors; implementations may be faked in tests."""

    def detect(self, frame: np.ndarray) -> list[Face]:
        """Return faces found in a BGR frame."""
        ...

    def close(self) -> None:
        """Release detector resources."""
        ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def yunet_model_path(settings: Settings) -> Path:
    """Return the YuNet model path, downloading and verifying it on first use."""
    model_dir = settings.data_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / MODEL_FILENAME
    if target.is_file() and target.stat().st_size > 0:
        if _sha256(target) == MODEL_SHA256:
            return target
        log.warning("YuNet model hash mismatch; re-downloading")
        target.unlink()
    log.warning("Downloading YuNet face model to %s (first run only)", target)
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=_DOWNLOAD_TIMEOUT) as resp:
            data = resp.read()
    except OSError as exc:
        raise ReframeError(
            "model_download_failed",
            f"Could not download the YuNet face model: {exc}. "
            "Check your connection or place the model manually.",
        ) from exc
    digest = hashlib.sha256(data).hexdigest()
    if digest != MODEL_SHA256:
        raise ReframeError(
            "model_hash_mismatch",
            "Downloaded YuNet model failed the SHA-256 integrity check.",
        )
    target.write_bytes(data)
    return target


class YuNetDetector:
    """YuNet face detector; OpenCV is imported lazily on construction."""

    def __init__(self, model_path: Path, score_threshold: float = 0.6) -> None:
        try:
            import cv2  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise ReframeError(
                "vision_unavailable",
                "opencv-python-headless is not installed; install the vision extra "
                '(`pip install "openclip[vision]"`) and run `openclip doctor`.',
            ) from exc
        self._cv2 = cv2
        self._detector: Any = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), score_threshold
        )

    def detect(self, frame: np.ndarray) -> list[Face]:
        """Detect faces in a BGR frame and return them in frame pixels."""
        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(frame)
        if faces is None:
            return []
        out: list[Face] = []
        for row in faces:
            # YuNet rows: x, y, w, h, 10 landmarks, score (last column).
            score = float(row[14] if len(row) > 14 else row[-1])
            if score <= 0:
                continue
            out.append(
                Face(
                    x=float(row[0]),
                    y=float(row[1]),
                    w=float(row[2]),
                    h=float(row[3]),
                    confidence=score,
                )
            )
        return out

    def close(self) -> None:
        """Release the detector (no-op; OpenCV manages its own state)."""
        self._detector = None
