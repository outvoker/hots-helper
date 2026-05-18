"""OCR entry point.

Single backend: RapidOCR (``rapidocr-onnxruntime``). Cross-platform
Python wheel, ships its CN+EN model weights inside the wheel, and we
ship two extra ONNX models alongside it (``ocr/models/``) so the same
pipeline reads Korean and Japanese text too. See
:mod:`hots_helper.ocr.rapid` for the multi-language implementation.

We deliberately don't fall back to system OCR (Vision on macOS,
Windows.Media.Ocr on Windows). Mixing engines per environment leads
to silently different recognition quality across squad members'
machines and made the BP popup show "missing data" reports that turned
out to be platform-dependent OCR misses. Sticking to one backend
keeps behaviour reproducible.
"""

from __future__ import annotations

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
              progress: ProgressCallback = None,
              languages: list[str] | None = None) -> list[OcrBlock]:
    """Run RapidOCR on ``image_path`` and return text blocks.

    ``languages`` is an optional list of engine tags (``"cn+en"``,
    ``"korean"``, ``"japanese"``); ``None`` runs every bundled
    engine. Callers in the UI thread it through from ``Config``.
    """
    try:
        from .rapid import recognize as _r
    except ImportError as e:
        if progress is not None:
            progress(f"rapidocr-onnxruntime not installed: {e}")
        return []
    return _r(image_path, progress=progress, languages=languages)
