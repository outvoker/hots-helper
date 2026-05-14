"""OCR backend selection."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ProgressCallback = Optional[Callable[[str], None]]


@dataclass
class OcrBlock:
    text: str
    # Normalized bbox in image coords: (x0, y0, x1, y1) with (0,0) = top-left.
    bbox: tuple[float, float, float, float]
    confidence: float


def recognize(image_path: Path,
              progress: ProgressCallback = None) -> list[OcrBlock]:
    """Run the best available OCR backend on ``image_path``.

    ``progress`` is an optional callback that receives stage strings as the
    pipeline runs (mainly useful on Windows where each step can take a
    couple seconds). The callback is invoked from the same thread as
    ``recognize`` itself.
    """
    if sys.platform == "darwin":
        try:
            from .vision_macos import recognize as _r
            return _r(image_path)  # macOS is fast enough that progress isn't needed
        except Exception:
            return []
    if sys.platform == "win32":
        try:
            from .winrt_ocr import recognize as _r
            return _r(image_path, progress=progress)
        except Exception:
            return []
    return []
