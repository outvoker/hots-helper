"""Windows OCR backend using ``Windows.Media.Ocr``.

Why this is annoying: ``Windows.Media.Ocr`` is an async Windows Runtime API.
Running it from a Python thread requires:

1. The thread to be inside a COM apartment (``CoInitializeEx``). Without
   this, every winrt call hangs forever — no error, no return.
2. An asyncio event loop to drive the IAsyncOperation completions. The
   default ``ProactorEventLoop`` on Windows doesn't work well from a
   non-main thread; we use a plain ``SelectorEventLoop`` instead.
3. Hard timeouts on each await, because hangs do happen and we don't
   want the UI worker to block the user forever.

A progress callback lets the worker thread stream stage messages to the UI
log even while the OCR is in flight.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from . import OcrBlock

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str], None]]

# Hard caps so a hung winrt call cannot freeze the worker thread forever.
_BITMAP_LOAD_TIMEOUT = 5.0
_PER_ENGINE_TIMEOUT = 8.0
_OVERALL_TIMEOUT = 30.0


def _emit(progress: ProgressCallback, msg: str) -> None:
    """Send ``msg`` to the UI log AND stderr / logger. Cheap belt-and-braces."""
    logger.info(msg)
    print(f"[winrt_ocr] {msg}", file=sys.stderr, flush=True)
    if progress is not None:
        try:
            progress(msg)
        except Exception:
            pass


# --- COM bootstrap ---------------------------------------------------------


def _init_com(progress: ProgressCallback) -> bool:
    """Initialize an STA on the current thread. Required for winrt."""
    try:
        import pythoncom  # provided by pywin32
    except ImportError:
        _emit(progress, "WARN pywin32 not installed — winrt may hang. "
                        "Run `uv sync` to pull pywin32.")
        return False
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        _emit(progress, "COM apartment initialized (STA)")
        return True
    except Exception as e:
        _emit(progress, f"WARN CoInitializeEx failed: {e}")
        return False


def _uninit_com() -> None:
    try:
        import pythoncom
        pythoncom.CoUninitialize()
    except Exception:
        pass


# --- Top-level runner ------------------------------------------------------


def _run_with_timeout(coro, progress: ProgressCallback):
    """Run ``coro`` on a Windows-friendly event loop with an overall timeout."""
    loop = None
    try:
        # SelectorEventLoop is the safest choice on a Qt worker thread.
        if sys.platform == "win32":
            loop = asyncio.SelectorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            asyncio.wait_for(coro, timeout=_OVERALL_TIMEOUT)
        )
    except asyncio.TimeoutError:
        _emit(progress, f"OCR overall timeout ({_OVERALL_TIMEOUT}s) — giving up")
        return []
    finally:
        if loop is not None:
            try:
                loop.close()
            except Exception:
                pass


# --- Image loading ---------------------------------------------------------


async def _load_bitmap(path: Path, progress: ProgressCallback):
    """Decode the image via Pillow, then construct a winrt SoftwareBitmap.

    We deliberately do NOT use ``BitmapDecoder.create_async``: on several
    winrt-python builds that call hangs forever (known interaction between
    winrt's IAsyncOperation and the COM apartment Qt's worker thread runs
    in). Pillow decodes to raw BGRA8 in pure Python; we then construct a
    SoftwareBitmap and copy the pixels in via ``copy_from_buffer``.
    """
    from PIL import Image
    from winrt.windows.graphics.imaging import (
        BitmapAlphaMode,
        BitmapPixelFormat,
        SoftwareBitmap,
    )
    from winrt.windows.storage.streams import DataWriter

    _emit(progress, f"reading {path.stat().st_size:,} bytes from disk")

    # Decode with Pillow into the BGRA8 layout SoftwareBitmap expects.
    _emit(progress, "decoding image with Pillow…")
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        # SoftwareBitmap BGRA8 wants B,G,R,A byte order. Swap R and B.
        r, g, b, a = rgba.split()
        bgra = Image.merge("RGBA", (b, g, r, a))
        width, height = bgra.size
        raw = bgra.tobytes()

    _emit(progress, f"image decoded: {width}x{height} ({len(raw):,} bytes)")

    # Pack the bytes into a winrt IBuffer.
    _emit(progress, "packing pixels into winrt Buffer…")
    writer = DataWriter()
    writer.write_bytes(raw)
    src_buffer = writer.detach_buffer()

    _emit(progress, "constructing SoftwareBitmap…")
    # Allocate the bitmap, then copy pixels into it. This avoids the
    # static ``create_copy_from_buffer`` factory whose signature differs
    # across winrt-python builds.
    bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, width, height,
                            BitmapAlphaMode.PREMULTIPLIED)
    bitmap.copy_from_buffer(src_buffer)
    _emit(progress, "SoftwareBitmap constructed")
    return bitmap


# --- Recognition -----------------------------------------------------------


_PREFERRED_LANGS = (
    "zh-Hans-CN", "zh-Hans", "zh-CN",
    "ja-JP", "ja",
    "ko-KR", "ko",
    "en-US",
)


def _create_engine(tag: str):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine
    try:
        return OcrEngine.try_create_from_language(Language(tag))
    except Exception:
        return None


def _enum_engines(progress: ProgressCallback):
    from winrt.windows.media.ocr import OcrEngine
    out = []
    seen = set()
    for tag in _PREFERRED_LANGS:
        if tag in seen:
            continue
        eng = _create_engine(tag)
        if eng is not None:
            out.append((tag, eng))
            seen.add(tag)
    if not out:
        try:
            eng = OcrEngine.try_create_from_user_profile_languages()
            if eng is not None:
                out.append(("user-profile", eng))
        except Exception:
            pass
    _emit(progress, f"available OCR engines: {[tag for tag, _ in out]}")
    if not out:
        _emit(progress, "ERROR no Windows OCR engine available — install at "
                        "least the English language pack")
    return out


async def _recognize_with_engine(engine, bitmap, tag: str,
                                 progress: ProgressCallback):
    blocks: list[OcrBlock] = []
    img_w = float(bitmap.pixel_width) or 1.0
    img_h = float(bitmap.pixel_height) or 1.0
    t = time.monotonic()
    try:
        result = await asyncio.wait_for(
            engine.recognize_async(bitmap),
            timeout=_PER_ENGINE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        _emit(progress, f"engine {tag!r} TIMEOUT after {_PER_ENGINE_TIMEOUT}s")
        return blocks, img_w, img_h
    except Exception as e:
        _emit(progress, f"engine {tag!r} crashed: {type(e).__name__}: {e}")
        return blocks, img_w, img_h

    if result is None:
        _emit(progress, f"engine {tag!r} returned None")
        return blocks, img_w, img_h

    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        line_text = line.text or ""
        if _is_cjk_only(line_text):
            text = "".join(w.text for w in words)
        else:
            text = " ".join(w.text for w in words)
        text = text.strip()
        if not text:
            continue
        x0 = min(w.bounding_rect.x for w in words)
        y0 = min(w.bounding_rect.y for w in words)
        x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
        y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
        blocks.append(
            OcrBlock(
                text=text,
                bbox=(x0 / img_w, y0 / img_h, x1 / img_w, y1 / img_h),
                confidence=1.0,
            )
        )
    _emit(progress,
          f"engine {tag!r}: {len(blocks)} blocks in "
          f"{time.monotonic() - t:.2f}s")
    return blocks, img_w, img_h


async def _recognize_async(path: Path, progress: ProgressCallback) -> list[OcrBlock]:
    bitmap = await _load_bitmap(path, progress)
    _emit(progress,
          f"bitmap ready: {bitmap.pixel_width}x{bitmap.pixel_height}")

    engines = _enum_engines(progress)
    if not engines:
        return []

    primary_tag, primary_engine = engines[0]
    primary_blocks, _w, _h = await _recognize_with_engine(
        primary_engine, bitmap, primary_tag, progress
    )

    # Stop after primary if we got enough blocks or there is no second engine.
    if len(primary_blocks) >= 3 or len(engines) == 1:
        return primary_blocks

    all_blocks = list(primary_blocks)
    for tag, engine in engines[1:4]:
        extra_blocks, _w, _h = await _recognize_with_engine(
            engine, bitmap, tag, progress
        )
        all_blocks.extend(extra_blocks)
    return _merge_overlapping(all_blocks)


# --- Block dedup -----------------------------------------------------------


def _merge_overlapping(blocks: list[OcrBlock]) -> list[OcrBlock]:
    if len(blocks) < 2:
        return blocks
    out: list[OcrBlock] = []
    used = [False] * len(blocks)
    for i, b in enumerate(blocks):
        if used[i]:
            continue
        best = b
        for j in range(i + 1, len(blocks)):
            if used[j]:
                continue
            if _bbox_overlap_ratio(b.bbox, blocks[j].bbox) > 0.7:
                used[j] = True
                if _cjk_score(blocks[j].text) > _cjk_score(best.text):
                    best = blocks[j]
        used[i] = True
        out.append(best)
    return out


def _cjk_score(text: str) -> int:
    return sum(1 for ch in text if _is_cjk_char(ch))


def _bbox_overlap_ratio(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0); iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1); iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    a_area = max(1e-9, (ax1 - ax0) * (ay1 - ay0))
    b_area = max(1e-9, (bx1 - bx0) * (by1 - by0))
    return inter / min(a_area, b_area)


def _is_cjk_only(text: str) -> bool:
    cjk = sum(1 for ch in text if _is_cjk_char(ch))
    return cjk * 2 > len(text)


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3040 <= code <= 0x309F     # Hiragana
        or 0x30A0 <= code <= 0x30FF  # Katakana
        or 0x4E00 <= code <= 0x9FFF  # CJK ideographs
        or 0x3400 <= code <= 0x4DBF  # CJK ideographs Ext-A
        or 0xAC00 <= code <= 0xD7AF  # Hangul syllables
        or 0x1100 <= code <= 0x11FF  # Hangul jamo
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
    )


# --- Public entry point ----------------------------------------------------


def recognize(image_path: Path,
              progress: ProgressCallback = None) -> list[OcrBlock]:
    """Run Windows OCR on ``image_path``.

    ``progress`` is an optional callback (called from the same thread) that
    receives stage messages as plain strings. Hook it up to your UI's log
    so the user can see exactly where the pipeline is spending time.
    """
    com_initialized = _init_com(progress)
    try:
        return _run_with_timeout(
            _recognize_async(image_path, progress),
            progress,
        )
    except Exception as e:
        _emit(progress, f"FATAL OCR pipeline crash: {type(e).__name__}: {e}")
        logger.exception("Windows OCR pipeline crashed")
        return []
    finally:
        if com_initialized:
            _uninit_com()
