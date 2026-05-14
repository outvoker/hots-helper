"""RapidOCR backend (PaddleOCR ported to ONNX Runtime).

Cross-platform, pure Python wheels, no system dependencies. Bundles around
12 MB of model weights, included in the wheel — works the same on Windows,
macOS, and Linux. Recognizes Chinese / English / Japanese / Korean from a
single model.

Why we use this instead of system OCR:
- Windows.Media.Ocr fails on the stylized HotS draft UI: the engine treats
  the colored hex glow + half-transparent name strips as "not text" and
  silently returns no blocks for player names.
- macOS Vision works but is platform-locked.

RapidOCR's CRNN model handles game UI text reliably and ports cleanly into
PyInstaller bundles without code-signing complications.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

from . import OcrBlock

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str], None]]

# Module-level singleton: building the OCR engine triggers ONNX Runtime model
# load + JIT, ~1s on a cold start. Reusing keeps subsequent screenshots fast.
_engine = None


def _emit(progress: ProgressCallback, msg: str) -> None:
    logger.info(msg)
    if progress is not None:
        try:
            progress(msg)
        except Exception:
            pass


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def recognize(image_path: Path,
              progress: ProgressCallback = None) -> list[OcrBlock]:
    t0 = time.monotonic()
    _emit(progress, "loading RapidOCR engine…")
    try:
        engine = _get_engine()
    except ImportError as e:
        _emit(progress, f"rapidocr-onnxruntime not installed: {e}")
        return []
    except Exception as e:
        _emit(progress, f"engine init failed: {type(e).__name__}: {e}")
        return []
    _emit(progress, f"engine ready ({time.monotonic() - t0:.2f}s)")

    # RapidOCR accepts file paths, byte arrays, or numpy arrays. Pass the
    # path; it handles JPEG/PNG decoding internally with OpenCV.
    t1 = time.monotonic()
    _emit(progress, f"running OCR on {image_path.name}…")
    try:
        result, _elapse = engine(str(image_path))
    except Exception as e:
        _emit(progress, f"OCR call failed: {type(e).__name__}: {e}")
        logger.exception("RapidOCR call failed")
        return []
    _emit(progress, f"OCR returned {len(result or [])} block(s) in "
                    f"{time.monotonic() - t1:.2f}s")

    if not result:
        return []

    # Resolve image dimensions for normalization. OpenCV decodes inside
    # RapidOCR but doesn't expose the size, so do a cheap PIL stat.
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            img_w, img_h = im.size
    except Exception as e:
        _emit(progress, f"PIL stat failed: {e} — using bbox extents as size")
        # Fall back to using the union of all detected boxes as the image
        # bounds. Slightly less accurate but still usable.
        all_xs, all_ys = [], []
        for box, _text, _conf in result:
            all_xs.extend(p[0] for p in box)
            all_ys.extend(p[1] for p in box)
        img_w = max(all_xs) if all_xs else 1.0
        img_h = max(all_ys) if all_ys else 1.0

    blocks: list[OcrBlock] = []
    for entry in result:
        box, text, conf = entry
        # box is a list of 4 (x, y) corner points (clockwise from top-left).
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0 = min(xs); x1 = max(xs)
        y0 = min(ys); y1 = max(ys)
        text = (text or "").strip()
        if not text:
            continue
        blocks.append(
            OcrBlock(
                text=text,
                bbox=(x0 / img_w, y0 / img_h, x1 / img_w, y1 / img_h),
                confidence=float(conf or 0.0),
            )
        )
    return blocks
