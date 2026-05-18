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

    Backend selection order:

    1. macOS / Windows system OCR ("vision" / "winrt"). They both
       handle Korean and Japanese natively when the language packs are
       installed (Vision: always; WinRT: per-user-installed packs).
    2. RapidOCR (PP-OCRv4 Chinese-English model) as fallback. RapidOCR
       does *not* recognize Hangul or Hiragana/Katakana — its bundled
       model is CN+EN only. Returning low-confidence garbage for
       Korean glyphs is worse than admitting we couldn't read them.

    The previous order was rapid → system, which caused KR / JP names
    to come out as random Chinese characters or empty strings on Mac
    (where Vision would have nailed them).

    ``HOTS_HELPER_OCR_BACKEND`` env var still overrides:
    * ``system``  — only system OCR, never rapid
    * ``rapid``   — only rapid, never system
    * ``auto`` (default) — system first, rapid fallback
    """
    backend = os.environ.get("HOTS_HELPER_OCR_BACKEND", "auto").lower()

    if backend == "rapid":
        # Forced rapid path (mostly for debugging — gives consistent
        # output across machines regardless of locale settings).
        try:
            from .rapid import recognize as _r
            return _r(image_path, progress=progress)
        except ImportError:
            return _system_recognize(image_path, progress)

    if backend == "system":
        return _system_recognize(image_path, progress)

    # Default: system first (better multi-language coverage), rapid fallback.
    blocks = _system_recognize(image_path, progress)
    if blocks:
        return blocks
    if progress is not None:
        progress("system OCR returned no blocks; falling back to RapidOCR")
    try:
        from .rapid import recognize as _r
        return _r(image_path, progress=progress)
    except ImportError:
        return []
