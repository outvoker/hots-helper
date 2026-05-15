"""Extract HotS in-game chat lines from a fullscreen screenshot.

The chat panel sits in the lower-left of the screen during a match,
roughly the bottom 25% (y) and left 35% (x) in normalized coordinates
on the standard 1080p layout. The exact bounds vary with resolution,
UI scale and whether the chat is fully expanded, so we use a generous
region and rely on text-shape heuristics to drop UI chrome / minimap
labels / merc-camp timers that happen to share that area.

Returns a list of (text, bbox, confidence) — same as ``OcrBlock`` —
so callers can show the line on top of the screenshot if they want
overlay-style rendering.

Heuristic flow:

1.  Filter to blocks whose center is inside the chat panel rectangle.
2.  Drop blocks dominated by digits or pure-symbol noise (timers,
    HUD numbers, "GG" alone — actually, keep "GG" since people do
    say it; only drop strings that are 100% non-letters).
3.  Drop blocks with ASCII brackets like ``[1.88]`` (channel tags
    that the chat client adds).
4.  Sort top→bottom so the rendered list reads in time order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ocr import OcrBlock


# Normalised chat panel — x0/y0/x1/y1 fractions of the screen.
# The bottom-left corner where the chat scroll lives.  Generous on the
# right edge because the chat input wraps over the central HUD; tight
# on the top to drop nameplate/voice-chat overlays.
CHAT_REGION = (0.00, 0.65, 0.45, 0.94)


@dataclass
class ChatLine:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float


# ASCII channel tags we drop entirely.
_CHANNEL_TAG_RE = re.compile(r"^\[[^\]]{1,12}\]$")
# Pure-numeric or pure-symbolic tokens — HUD timers ("00:42"), merc
# camp counters ("3"), minimap pings ("!"), etc.
_NON_TEXT_RE = re.compile(r"^[\s\d:./\-+]+$")


def _in_chat_region(block: OcrBlock) -> bool:
    x0, y0, x1, y1 = block.bbox
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    rx0, ry0, rx1, ry1 = CHAT_REGION
    return rx0 <= cx <= rx1 and ry0 <= cy <= ry1


def _looks_like_chat(text: str) -> bool:
    s = text.strip()
    if len(s) < 2:
        return False
    if _CHANNEL_TAG_RE.match(s):
        return False
    if _NON_TEXT_RE.match(s):
        return False
    # Must contain at least one alphabetic / CJK character somewhere.
    for ch in s:
        if ch.isalpha():
            return True
    return False


def extract_chat_lines(blocks: list[OcrBlock]) -> list[ChatLine]:
    """Pick out chat lines from a list of OCR blocks (sorted top→bottom)."""
    candidates = [b for b in blocks if _in_chat_region(b) and _looks_like_chat(b.text)]
    candidates.sort(key=lambda b: ((b.bbox[1] + b.bbox[3]) / 2))
    return [
        ChatLine(text=b.text.strip(), bbox=b.bbox, confidence=b.confidence)
        for b in candidates
    ]
