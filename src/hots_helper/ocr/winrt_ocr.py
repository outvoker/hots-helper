"""Windows OCR backend using ``Windows.Media.Ocr``.

The Windows 10/11 system OCR engine — same one Snip & Sketch uses. Handles
English + the CJK languages whose packs are installed (Settings → Time &
Language → Add a language → 中文(简体)/日本語/한국어 → Optional features →
"Basic typing" includes the OCR data).

We optimize for fast first response:

- Read the image straight from disk into an in-memory ``InMemoryRandomAccessStream``
  (StorageFile.get_file_from_path_async has been observed to hang on Windows
  when called from a non-UI thread).
- Initialize a COM apartment ourselves before any winrt call. Without this,
  ``Windows.Media.Ocr`` deadlocks instead of erroring.
- Try one engine first (the user's CN preference). Only fan out to extra
  languages if the first pass returns < 3 blocks — multi-language is
  expensive and the second pass is rarely needed when the user has the
  right pack installed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from . import OcrBlock

logger = logging.getLogger(__name__)


# --- Top-level COM + asyncio runner ----------------------------------------


def _run(coro):
    """Run ``coro`` to completion. Initializes a COM apartment first.

    The whole point: ``Windows.Media.Ocr`` is COM-based. The thread that
    calls it must already be inside a COM apartment, otherwise the call
    blocks forever (no error, no return). We use STA because winrt is built
    on apartment-threaded COM.
    """
    initialized = False
    try:
        import pythoncom

        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
            logger.debug("CoInitializeEx STA succeeded")
        except Exception as e:
            logger.warning("CoInitializeEx failed: %s — continuing anyway", e)
    except ImportError:
        logger.debug("pythoncom not available; skipping COM init")

    try:
        return asyncio.new_event_loop().run_until_complete(coro)
    finally:
        if initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass


# --- Image loading ---------------------------------------------------------


async def _load_bitmap(path: Path):
    """Read the image as a SoftwareBitmap.

    We avoid StorageFile.get_file_from_path_async because it requires the
    UI thread / capabilities on packaged apps and has been observed to hang
    on plain Win32 processes. Instead we feed the bytes directly to a
    DataWriter -> InMemoryRandomAccessStream.
    """
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import (
        DataWriter,
        InMemoryRandomAccessStream,
    )

    raw = path.read_bytes()
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    try:
        writer.write_bytes(raw)
        await writer.store_async()
    finally:
        # Detach so the underlying stream stays valid for the decoder.
        try:
            writer.detach_stream()
        except Exception:
            pass
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    return await decoder.get_software_bitmap_async()


# --- Recognition -----------------------------------------------------------


# Order matters: Chinese first since this is the primary use-case. en-US is
# a backstop because it's installed by default on every Windows machine.
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
        lang = Language(tag)
        return OcrEngine.try_create_from_language(lang)
    except Exception:
        return None


def _enum_engines():
    """Return list of ``(tag, engine)`` for every available preferred language."""
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
    return out


async def _recognize_with_engine(engine, bitmap):
    blocks = []
    img_w = float(bitmap.pixel_width) or 1.0
    img_h = float(bitmap.pixel_height) or 1.0
    result = await engine.recognize_async(bitmap)
    if result is None:
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
    return blocks, img_w, img_h


async def _recognize_async(path: Path) -> list[OcrBlock]:
    t0 = time.monotonic()
    logger.info("loading bitmap from %s", path)
    bitmap = await _load_bitmap(path)
    logger.info("bitmap loaded in %.2fs (%dx%d)",
                time.monotonic() - t0, bitmap.pixel_width, bitmap.pixel_height)

    engines = _enum_engines()
    logger.info("available OCR engines: %s", [tag for tag, _ in engines])
    if not engines:
        logger.error("no Windows OCR engine available — install language packs")
        return []

    # Fast path: try the first engine. If it returns enough blocks, stop.
    primary_tag, primary_engine = engines[0]
    t1 = time.monotonic()
    primary_blocks, _w, _h = await _recognize_with_engine(primary_engine, bitmap)
    logger.info("primary engine %r: %d blocks in %.2fs",
                primary_tag, len(primary_blocks), time.monotonic() - t1)

    if len(primary_blocks) >= 3 or len(engines) == 1:
        return primary_blocks

    # Fan out to remaining engines for extra coverage (CJK names that the
    # primary engine garbled). Cap to 3 extra engines so we don't burn
    # 10 seconds on a slow box.
    all_blocks = list(primary_blocks)
    for tag, engine in engines[1:4]:
        t2 = time.monotonic()
        try:
            extra_blocks, _w, _h = await _recognize_with_engine(engine, bitmap)
            logger.info("engine %r: %d blocks in %.2fs",
                        tag, len(extra_blocks), time.monotonic() - t2)
            all_blocks.extend(extra_blocks)
        except Exception as e:
            logger.warning("engine %r failed: %s", tag, e)
    return _merge_overlapping(all_blocks)


# --- Block dedup -----------------------------------------------------------


def _merge_overlapping(blocks: list[OcrBlock]) -> list[OcrBlock]:
    """Dedup blocks from multiple OCR passes covering the same region.

    Overlap > 70% → keep the candidate with more CJK characters (the CJK
    engine's read of a Chinese name beats the en-US engine's gibberish).
    """
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


def recognize(image_path: Path) -> list[OcrBlock]:
    try:
        return _run(_recognize_async(image_path))
    except Exception:
        logger.exception("Windows OCR pipeline crashed")
        return []
