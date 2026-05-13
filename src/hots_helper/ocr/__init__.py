"""OCR backend selection."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OcrBlock:
    text: str
    # Normalized bbox in image coords: (x0, y0, x1, y1) with (0,0) = top-left.
    bbox: tuple[float, float, float, float]
    confidence: float


def recognize(image_path: Path) -> list[OcrBlock]:
    """Run the best available OCR backend on ``image_path``.

    Returns a list of text blocks with normalized positions. Empty list on
    failure or when no backend is available.
    """
    if sys.platform == "darwin":
        try:
            from .vision_macos import recognize as _r
            return _r(image_path)
        except Exception:
            return []
    if sys.platform == "win32":
        try:
            from .winrt_ocr import recognize as _r
            return _r(image_path)
        except Exception:
            return []
    # Fall back: no OCR.
    return []
