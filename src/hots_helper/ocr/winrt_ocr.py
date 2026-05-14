"""Windows OCR backend using ``Windows.Media.Ocr``.

This is the system OCR shipped with Windows 10/11 — same engine that powers
Snip & Sketch text recognition. It handles English + Chinese well as long as
the matching language pack is installed (Settings → Time & Language → Add a
language → 中文(简体, 中国) → Optional features → ensure "Basic typing"
includes the OCR pack).

Requires the ``winrt`` Python wrapper. Installed by ``pip install winrt-runtime
winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging
winrt-Windows.Storage.Streams winrt-Windows.Globalization``. The build is
scoped to ``sys_platform == 'win32'`` in ``pyproject.toml`` so macOS/Linux
checkouts skip these.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import OcrBlock


def _run(coro):
    """Run ``coro`` to completion regardless of the current event loop state.

    Each call also initializes a COM apartment for the calling thread. Without
    this, ``Windows.Media.Ocr`` calls hang indefinitely on threads other than
    the one that first imported pythonnet/winrt — which on Qt apps means a
    deadlock the first time the user invokes OCR from anywhere except the
    initializer. We use STA because winrt expects an apartment-threaded host;
    failures are non-fatal (e.g. the thread might already have an apartment).
    """
    try:
        import pythoncom  # provided by pywin32 on Windows

        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except Exception:
            initialized = False
    except ImportError:
        # pywin32 isn't a hard dep; if it's missing, fall back without COM init.
        # Most modern winrt builds will succeed on a thread the runtime itself
        # initializes lazily.
        initialized = False

    try:
        try:
            return asyncio.run(coro)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
    finally:
        if initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass


async def _recognize_async(path: Path) -> list[OcrBlock]:
    # Imports are inside the function so the top-level module load stays cheap
    # on systems where the winrt packages might be missing.
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import StorageFile

    file = await StorageFile.get_file_from_path_async(str(path.resolve()))
    stream = await file.open_async(0)  # FileAccessMode.Read
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
    finally:
        stream.close()

    # Windows.Media.Ocr is single-language per OcrEngine instance. To handle
    # mixed CJK names (Chinese + Japanese + Korean) in the same screenshot,
    # we run the recognizer once per installed CJK language and merge results
    # by position, keeping whichever pass produced the most "interesting"
    # block at each location.
    from winrt.windows.globalization import Language

    engines = []
    seen_tags: set[str] = set()

    # Preferred languages, in priority order.
    preferred_tags = (
        "zh-Hans-CN", "zh-Hans", "zh-CN",
        "ja", "ja-JP",
        "ko", "ko-KR",
        "en-US",
    )
    for tag in preferred_tags:
        if tag in seen_tags:
            continue
        try:
            lang = Language(tag)
            eng = OcrEngine.try_create_from_language(lang)
            if eng is not None:
                engines.append((tag, eng))
                seen_tags.add(tag)
        except Exception:
            continue

    # Backstop: whatever the user has installed.
    if not engines:
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is not None:
            engines.append(("user-profile", eng))

    if not engines:
        return []

    img_w = float(bitmap.pixel_width) or 1.0
    img_h = float(bitmap.pixel_height) or 1.0

    # Collect all blocks from all engines. We'll dedup near-identical bboxes
    # later, preferring the result that contains more CJK characters when one
    # engine read text but another only saw garbled latin.
    all_blocks: list[OcrBlock] = []
    for tag, engine in engines:
        try:
            result = await engine.recognize_async(bitmap)
        except Exception:
            continue
        if result is None:
            continue
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
            all_blocks.append(
                OcrBlock(
                    text=text,
                    bbox=(x0 / img_w, y0 / img_h, x1 / img_w, y1 / img_h),
                    confidence=1.0,
                )
            )

    return _merge_overlapping(all_blocks)


def _merge_overlapping(blocks: list[OcrBlock]) -> list[OcrBlock]:
    """Dedup blocks from multiple OCR passes that cover the same region.

    When two blocks overlap by >70%, keep the one whose text has more CJK
    characters (better recognition by a CJK-specific engine).
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
    """Higher = more likely to be a real CJK string."""
    return sum(1 for ch in text if _is_cjk_char(ch))


def _bbox_overlap_ratio(a: tuple[float, float, float, float],
                        b: tuple[float, float, float, float]) -> float:
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
    """Return True if the line is composed mostly of CJK characters.

    Covers:
    - CJK Unified Ideographs (Chinese / Japanese kanji / Korean hanja)
    - Hiragana + Katakana (Japanese kana)
    - Hangul syllables (Korean)
    """
    cjk = sum(1 for ch in text if _is_cjk_char(ch))
    return cjk * 2 > len(text)


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3040 <= code <= 0x309F     # Hiragana
        or 0x30A0 <= code <= 0x30FF  # Katakana
        or 0x4E00 <= code <= 0x9FFF  # CJK ideographs (most common Han)
        or 0x3400 <= code <= 0x4DBF  # CJK ideographs Ext-A
        or 0xAC00 <= code <= 0xD7AF  # Hangul syllables
        or 0x1100 <= code <= 0x11FF  # Hangul jamo
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
    )


def recognize(image_path: Path) -> list[OcrBlock]:
    try:
        return _run(_recognize_async(image_path))
    except Exception:
        return []
