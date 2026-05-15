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
#
# Empirically the in-game chat scroll sits in the bottom-center of the
# screen, just above the chat input box. On 1080p the message area
# centers around (x≈0.5, y≈0.78) and is roughly 0.45 wide.  We use a
# generous box because resolution / UI scale move things around;
# heuristic text filters in :func:`_looks_like_chat` clean up the
# false-positives (HUD timers, ping count, the chat *input* placeholder
# "按下回车键或'/'键开始聊天" itself, etc.).
CHAT_REGION = (0.25, 0.62, 0.78, 0.92)


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
# Substrings that mark *chat-adjacent UI* — the input-box placeholder,
# BP-screen buttons that fall inside our chat region, etc. Anything
# containing one of these exact tokens is dropped. Keep this list
# short and Chinese-canonical (CN client) — adding aggressive English
# filters risks dropping real chat that quotes UI labels.
_INPUT_PLACEHOLDERS = (
    "按下回车键",
    "按回车键",
    "Press Enter",
    "press enter",
    "/键开始聊天",
    "to chat",
    # BP-screen artefacts that overlap our chat region.
    "查看所有英雄",          # "View all heroes" button
    "正在等待敌方禁用英雄",  # status banner during enemy ban
    "正在等待队伍禁用英雄",  # status banner during ally ban
    "正在选择禁用英雄",      # drafter status text
    "正在选择英雄",
    # Voice chat status, kicker / joiner system messages — these
    # *are* legitimate game chat, but they're system-generated and
    # not what the user wants translated. Kept narrow on purpose.
    "团队语音可用",
    "加入团队语音",
)


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
    for placeholder in _INPUT_PLACEHOLDERS:
        if placeholder in s:
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


def filter_chat_blocks(blocks: list[OcrBlock]) -> list[ChatLine]:
    """Apply only the *content* heuristics (no region check) to a list of
    blocks the user already cropped down to the chat area manually.

    Used by the "redraw chat region" path in the translation popup —
    once the user has dragged a tight box, every block in that box is
    presumed to be inside the chat panel, so the region check would
    be redundant (and on small crops, occasionally too strict).
    """
    candidates = [b for b in blocks if _looks_like_chat(b.text)]
    candidates.sort(key=lambda b: ((b.bbox[1] + b.bbox[3]) / 2))
    return [
        ChatLine(text=b.text.strip(), bbox=b.bbox, confidence=b.confidence)
        for b in candidates
    ]
