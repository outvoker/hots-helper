"""OCR backend selection.

Default: RapidOCR (rapidocr-onnxruntime). Cross-platform Python wheel,
includes its model weights, recognizes CN/EN/JP/KR with high accuracy on
stylized game UI text. This is the only backend we ship to end users.

System OCR (Vision on macOS, Windows.Media.Ocr on Windows) is kept as a
fallback for environments where RapidOCR can't be installed (e.g. an
extremely locked-down corporate machine). Disabled by default — set
``HOTS_HELPER_OCR_BACKEND=system`` to opt in.
"""

from __future__ import annotations

import os
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


def _system_recognize(image_path: Path,
                      progress: ProgressCallback) -> list[OcrBlock]:
    if sys.platform == "darwin":
        try:
            from .vision_macos import recognize as _r
            return _r(image_path)
        except Exception:
            return []
    if sys.platform == "win32":
        try:
            from .winrt_ocr import recognize as _r
            return _r(image_path, progress=progress)
        except Exception:
            return []
    return []


def recognize(image_path: Path,
              progress: ProgressCallback = None) -> list[OcrBlock]:
    """Run the best available OCR backend on ``image_path``.

    ``progress`` is an optional callback that receives stage strings as the
    pipeline runs. The callback is invoked from the same thread as
    ``recognize`` itself.
    """
    backend = os.environ.get("HOTS_HELPER_OCR_BACKEND", "rapid").lower()

    if backend == "rapid":
        try:
            from .rapid import recognize as _r
            blocks = _r(image_path, progress=progress)
            if blocks:
                return blocks
            # If RapidOCR returns nothing (rare), fall back to the system
            # engine instead of giving up immediately.
            if progress is not None:
                progress("RapidOCR returned no blocks; trying system OCR fallback")
            return _system_recognize(image_path, progress)
        except ImportError:
            if progress is not None:
                progress("rapidocr-onnxruntime not installed; falling back to system OCR")
            return _system_recognize(image_path, progress)

    return _system_recognize(image_path, progress)
