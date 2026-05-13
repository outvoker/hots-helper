"""macOS Vision framework OCR backend.

Recognizes text in an image using the built-in system recognizer, which
handles Chinese + Latin and the stylized HotS UI font well. Returns all
recognized text blocks with normalized bounding boxes.

Available since macOS 10.15. No additional model downloads needed.
"""

from __future__ import annotations

from pathlib import Path

from Foundation import NSURL
import Vision
import Quartz

from . import OcrBlock


def _load_cgimage(path: Path) -> Quartz.CGImageRef:
    url = NSURL.fileURLWithPath_(str(path))
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        raise RuntimeError(f"cannot open image: {path}")
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def recognize(image_path: Path) -> list[OcrBlock]:
    cg_image = _load_cgimage(image_path)
    if cg_image is None:
        return []

    request = Vision.VNRecognizeTextRequest.alloc().init()
    # Accurate mode handles the stylized font better than "fast".
    request.setRecognitionLevel_(0)  # VNRequestTextRecognitionLevelAccurate
    request.setUsesLanguageCorrection_(False)
    # Hint at the languages we expect. macOS picks up CJK and Latin as
    # separate recognizers; listing both is a no-op if the version doesn't
    # support a particular language.
    try:
        # Order matters here: Vision tokenizes to whichever language model
        # claims a glyph first, so list CJK models before en-US so e.g. "Banker"
        # written by a Korean user doesn't get downgraded to alphabetic-only.
        request.setRecognitionLanguages_([
            "zh-Hans", "zh-Hant", "ja-JP", "ko-KR", "en-US",
        ])
    except Exception:
        pass

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        return []

    out: list[OcrBlock] = []
    for obs in (request.results() or []):
        top = obs.topCandidates_(1)
        if not top:
            continue
        cand = top[0]
        text = cand.string()
        if not text:
            continue
        # Vision bbox is in normalized image coords where origin is BOTTOM-LEFT
        # and y grows upward. Flip to top-left origin so callers can treat it
        # as PIL-style.
        bb = obs.boundingBox()
        x0 = float(bb.origin.x)
        y0_bot = float(bb.origin.y)
        ww = float(bb.size.width)
        hh = float(bb.size.height)
        x1 = x0 + ww
        # Flip y.
        y0 = max(0.0, 1.0 - (y0_bot + hh))
        y1 = 1.0 - y0_bot
        out.append(
            OcrBlock(
                text=str(text),
                bbox=(x0, y0, x1, y1),
                confidence=float(cand.confidence()),
            )
        )
    return out
