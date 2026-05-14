"""Screenshot → (map, ally names, enemy names) using the system OCR backend.

macOS Vision / Windows.Media.Ocr return every text block in the image with
normalized bounding boxes. We then bucket blocks by position:

- Top-center band (y < ~12%, x roughly centered) → map name.
- Left band (x < ~15%) → ally names; sort by y, take 5.
- Right band (x > ~85%) → enemy names; sort by y, take 5.

No calibration needed as long as the image is a real BP-phase screenshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ocr import OcrBlock, recognize


# --- tunables ----------------------------------------------------------------

_LEFT_MAX_X = 0.18    # blocks whose center-x is below this fall into the ally column
_RIGHT_MIN_X = 0.82   # blocks whose center-x is above this fall into the enemy column
_MAP_TOP_MAX_Y = 0.10
_MAP_CENTER_X_MIN = 0.35
_MAP_CENTER_X_MAX = 0.65

# Each side has 5 hex slots arranged vertically; their name-strip y centers
# (normalized) measured against the in-game BP screen. We snap detected blocks
# to their nearest expected row, dropping anything that drifts off — that
# eliminates voice-chat overlays (e.g. Kook's "current speaker" indicator)
# whose y position falls between rows.
_SLOT_Y_CENTERS = (0.20, 0.36, 0.52, 0.67, 0.82)
_SLOT_Y_TOLERANCE = 0.05

# Center stage: the player currently picking is drawn near the middle of the
# screen with a "正在选择..." caption. We surface this name separately so the
# UI can hint at it.
_DRAFTER_X_RANGE = (0.40, 0.60)
_DRAFTER_Y_RANGE = (0.45, 0.60)
# BP-phase UI text. Keep an explicit ignore list of phrases we've seen on the
# Chinese client; for other locales we rely on the heuristic in
# ``_is_probably_ui_chrome`` (sentence-like punctuation + length cutoff).
_IGNORE_PHRASES = {
    # zh-CN
    "正在选择禁用英雄", "正在等待队伍禁用英雄..", "正在等待队伍禁用英雄…",
    "查看所有英雄", "禁用英雄", "选择英雄",
    # en (NA/EU clients)
    "View all heroes", "Choosing ban...", "Waiting for ban...",
    "Selecting...", "Picking...",
    # ko (KR client)
    "모든 영웅 보기", "영웅 선택", "영웅 금지",
    # ja (TW/JP fallback)
    "すべてのヒーローを見る", "ヒーローを選択", "ヒーローを禁止",
}

# Player names are short and don't contain sentence-ending punctuation. Lines
# carrying any of these are UI chrome / chat lines.
_SENTENCE_PUNCT = set("，。！？、…,.!?;；:：")
_MAX_NAME_LEN = 18


@dataclass
class ParsedScreenshot:
    map_name: str
    ally_names: list[str]
    enemy_names: list[str]
    # Per-slot OCR confidence in [0, 1]. Missing slot → 0.
    map_confidence: float = 0.0
    ally_confidences: list[float] = None  # type: ignore[assignment]
    enemy_confidences: list[float] = None  # type: ignore[assignment]
    # Name of the player currently in the draft spotlight (shown in screen
    # center alongside "正在选择禁用英雄" etc.). Empty string when no banner
    # is detected.
    drafter: str = ""

    def __post_init__(self) -> None:
        if self.ally_confidences is None:
            self.ally_confidences = [0.0] * 5
        if self.enemy_confidences is None:
            self.enemy_confidences = [0.0] * 5

    @property
    def anything_found(self) -> bool:
        return bool(self.map_name) or any(self.ally_names) or any(self.enemy_names)


def _center_x(bbox: tuple[float, float, float, float]) -> float:
    x0, _, x1, _ = bbox
    return (x0 + x1) / 2


def _is_probably_ui_chrome(text: str) -> bool:
    """Heuristic: is this OCR line UI chrome rather than a player name?

    Aimed to be locale-independent. Specific phrases we've seen on real
    clients are listed in ``_IGNORE_PHRASES``; everything else relies on
    structural signals: length, sentence punctuation, brackets, all-digit
    timestamps.
    """
    t = text.strip()
    if not t:
        return True
    if t in _IGNORE_PHRASES:
        return True
    # Drop strings that look like sentences: containing sentence-ending
    # punctuation, or repeated dots/ellipsis used by "Waiting..." messages.
    if any(ch in _SENTENCE_PUNCT for ch in t):
        return True
    if "..." in t or ".." in t:
        return True
    # Drop bracketed UI like "[General]" or chat tags "[1.88]".
    if "[" in t or "]" in t or "【" in t or "】" in t:
        return True
    # Drop strings that are >70% digits + colon (timestamps "23:12", scores).
    digits = sum(1 for ch in t if ch.isdigit() or ch in ":.")
    if digits >= max(2, int(len(t) * 0.7)):
        return True
    # Player names are short. Cap at MAX_NAME_LEN to drop chat lines.
    if len(t) > _MAX_NAME_LEN:
        return True
    return False


def _pick_map(blocks: list[OcrBlock]) -> tuple[str, float]:
    candidates = [
        b for b in blocks
        if b.bbox[1] < _MAP_TOP_MAX_Y
        and _MAP_CENTER_X_MIN < _center_x(b.bbox) < _MAP_CENTER_X_MAX
        and not _is_probably_ui_chrome(b.text)
    ]
    if not candidates:
        return "", 0.0
    candidates.sort(key=lambda b: (-b.confidence, b.bbox[1]))
    best = candidates[0]
    return best.text.strip(), float(best.confidence)


def _block_center_y(bbox: tuple[float, float, float, float]) -> float:
    return (bbox[1] + bbox[3]) / 2


def _select_best_grid(clusters: list[OcrBlock], n: int = 5) -> list[OcrBlock]:
    """Pick the ``n`` blocks whose y positions best fit an evenly-spaced grid.

    Real slot rows are equidistant; overlays (Kook's "current speaker"
    chip, drafter spotlight) live off-grid. We brute-force every length-n
    subset of clusters, score each by ``sum((cy_i - expected_i)^2)`` where
    ``expected`` is a fitted linear sequence ``a + i*d`` over the subset's
    ys, and return the lowest-score subset.

    Cost: O(C(len, n)). Fine for n=5 and len typically <= 8.
    """
    from itertools import combinations

    ys = [_block_center_y(b.bbox) for b in clusters]
    best_score = float("inf")
    best_subset: list[int] = list(range(min(n, len(clusters))))

    for combo in combinations(range(len(clusters)), n):
        sub_ys = [ys[i] for i in combo]
        # Fit y_i ≈ a + d * i where i = 0..n-1.
        d = (sub_ys[-1] - sub_ys[0]) / (n - 1) if n > 1 else 0
        a = sub_ys[0]
        residual = sum((sub_ys[i] - (a + d * i)) ** 2 for i in range(n))
        # Penalty for extremely uneven spacing (very short or very tall):
        # legit slots span at least ~50% of the available height.
        span = sub_ys[-1] - sub_ys[0]
        if span < 0.40:
            residual += (0.40 - span) ** 2
        # Bonus for higher average confidence so a high-conf set wins ties.
        avg_conf = sum(clusters[i].confidence for i in combo) / n
        residual -= avg_conf * 0.001

        if residual < best_score:
            best_score = residual
            best_subset = list(combo)

    return [clusters[i] for i in best_subset]


def _pick_side(blocks: list[OcrBlock], side: str) -> tuple[list[str], list[float]]:
    """Pull up to 5 player names from the left or right column.

    Different displays / aspect ratios push the slot Y centers around
    enough that a hard-coded list of expected positions misses them. We
    instead detect the actual hex layout from the screenshot:

    1. Filter blocks that fall in the side's x band and aren't UI chrome.
    2. Sort top→bottom.
    3. Greedily group blocks separated by less than half the typical
       inter-slot gap (~0.16 of image height); within a group, keep the
       highest-confidence block. This drops voice-chat overlays / Kook's
       "current speaker" banner because they sit between two real slots
       at less than half-distance from one of them.
    4. Pad / truncate to exactly 5 names.
    """
    def in_side(b: OcrBlock) -> bool:
        cx = _center_x(b.bbox)
        if side == "L":
            return cx < _LEFT_MAX_X
        return cx > _RIGHT_MIN_X

    side_blocks = [
        b for b in blocks
        if in_side(b)
        and 0.10 < _block_center_y(b.bbox) < 0.92
        and not _is_probably_ui_chrome(b.text)
    ]
    side_blocks.sort(key=lambda b: _block_center_y(b.bbox))

    # Step A: collapse near-duplicates produced by two OCR engines reading
    # the same row.
    _DEDUP_GAP = 0.04
    clusters: list[OcrBlock] = []
    for b in side_blocks:
        cy = _block_center_y(b.bbox)
        if clusters and cy - _block_center_y(clusters[-1].bbox) < _DEDUP_GAP:
            if b.confidence > clusters[-1].confidence:
                clusters[-1] = b
            continue
        clusters.append(b)

    # Step B: pick the 5 candidates that best fit an evenly-spaced grid.
    # The five hex slots are vertically equidistant, so the right answer
    # minimizes "deviation from a perfect 5-step linear grid". Outliers like
    # Kook's voice-chat chip score badly because their y position breaks
    # the equal-spacing structure.
    if len(clusters) > 5:
        clusters = _select_best_grid(clusters, n=5)

    names = [b.text.strip() for b in clusters]
    confs = [float(b.confidence) for b in clusters]
    while len(names) < 5:
        names.append("")
        confs.append(0.0)
    return names, confs


def _pick_drafter(blocks: list[OcrBlock]) -> str:
    """Find the spotlight player in screen center (just above the caption)."""
    cx_min, cx_max = _DRAFTER_X_RANGE
    cy_min, cy_max = _DRAFTER_Y_RANGE
    candidates = [
        b for b in blocks
        if cx_min < _center_x(b.bbox) < cx_max
        and cy_min < _block_center_y(b.bbox) < cy_max
        and not _is_probably_ui_chrome(b.text)
        and len(b.text.strip()) <= _MAX_NAME_LEN
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda b: (-b.confidence, b.bbox[1]))
    return candidates[0].text.strip()


def parse_screenshot(
    image_path: Path,
    *,
    blocks: list[OcrBlock] | None = None,
) -> ParsedScreenshot:
    """Full screenshot → (map, ally_names, enemy_names, drafter) + confidences.

    If ``blocks`` is provided, skip OCR and reuse them — this avoids running
    Windows.Media.Ocr twice when the worker has already done it.
    """
    if blocks is None:
        blocks = recognize(image_path)
    if not blocks:
        return ParsedScreenshot(
            map_name="", ally_names=[""] * 5, enemy_names=[""] * 5,
            map_confidence=0.0,
            ally_confidences=[0.0] * 5,
            enemy_confidences=[0.0] * 5,
            drafter="",
        )
    map_name, map_conf = _pick_map(blocks)
    ally_names, ally_confs = _pick_side(blocks, "L")
    enemy_names, enemy_confs = _pick_side(blocks, "R")
    drafter = _pick_drafter(blocks)
    return ParsedScreenshot(
        map_name=map_name,
        ally_names=ally_names,
        enemy_names=enemy_names,
        map_confidence=map_conf,
        ally_confidences=ally_confs,
        enemy_confidences=enemy_confs,
        drafter=drafter,
    )
