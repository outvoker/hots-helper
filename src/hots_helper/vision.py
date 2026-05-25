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

from .heroes import is_hero_name
from .ocr import OcrBlock, recognize, recognize_array


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
    "正在选择中", "正在选择中..", "正在选择中…",
    "正在选择", "正在等待玩家", "正在等待玩家...",
    "正在等待玩家", "等待中", "我方队伍", "敌方队伍",
    # en (NA/EU clients)
    "View all heroes", "Choosing ban...", "Waiting for ban...",
    "Selecting...", "Picking...", "Choosing...",
    "Allied team", "Enemy team",
    # ko (KR client)
    "모든 영웅 보기", "영웅 선택", "영웅 금지",
    "선택 중", "선택 중...", "기다리는 중",
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

    Hero names are explicitly *not* dropped by this function — the
    side-picking pass deals with them via :func:`is_hero_name` so it
    can use the hero-name positions as Y anchors when the player-name
    band itself was missed.
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


def _pick_side(
    blocks: list[OcrBlock], side: str
) -> tuple[list[str], list[float], list[float]]:
    """Pull up to 5 player names from the left or right column.

    The BP UI shows two short labels per slot:

    * the **player name** strip (top of the hex avatar, dim text),
    * the **hero name** caption (below the avatar, brighter, larger).

    OCR confidence on the hero name is usually higher than on the
    player name strip, so a naive "best 5 by confidence" pass picks
    every hero-name block when both are detected.

    To get back to player names we:

    1. Filter blocks to the side's x band, drop UI chrome.
    2. **Find the 5 slot Y centers** using whichever signal is
       cleaner — the hero-name blocks (when heroes have been picked,
       which is when this is hardest) provide great y-anchors
       because they're consistently detected.
    3. For each slot, pick the highest-confidence non-hero-name block
       that falls within the slot's vertical tolerance. If only a
       hero-name block is present in that slot (no player-name OCR
       hit at all), leave the slot empty and let the per-slot
       fallback handle it.
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
    # the same row. Critically, the dedup only fires when both blocks
    # are the *same kind* — hero names sit ~0.025 above player names on
    # the same slot, which used to look like a dup at the previous
    # 0.04 gap and silently swallowed the player-name detection.
    _DEDUP_GAP = 0.012
    clusters: list[OcrBlock] = []
    for b in side_blocks:
        cy = _block_center_y(b.bbox)
        b_is_hero = is_hero_name(b.text)
        if clusters:
            prev = clusters[-1]
            prev_cy = _block_center_y(prev.bbox)
            prev_is_hero = is_hero_name(prev.text)
            if (
                b_is_hero == prev_is_hero
                and cy - prev_cy < _DEDUP_GAP
            ):
                if b.confidence > prev.confidence:
                    clusters[-1] = b
                continue
        clusters.append(b)

    # Step B: derive the 5 slot Y centers.
    #
    # Strategy: hero-name blocks are by far the most reliably detected
    # signal in this column (high contrast, fixed font), so when we
    # have ≥3 hero-name detections we use *their* Y values as the
    # anchor grid. Each player-name strip sits ~0.025 above the hero
    # caption (empirically measured against current screenshots),
    # so we shift the anchors up by that offset.
    hero_blocks = [c for c in clusters if is_hero_name(c.text)]
    non_hero_blocks = [c for c in clusters if not is_hero_name(c.text)]

    # Vertical offset from hero-caption-center to player-name-strip-center.
    # Positive = player name is *below* the hero caption — the hex avatar
    # sits between them with the hero name above and the player name on
    # the bottom strip. Calibrated against current screenshots: BeigeBison
    # at 0.357 vs hero "阿兹莫丹" at 0.329 → +0.028.
    _PLAYER_BELOW_HERO = 0.028

    slot_ys: list[float]
    if len(hero_blocks) >= 2:
        # Fit slot ys from hero positions. We need 5 slots — when fewer
        # than 5 heroes have been picked the others are still
        # equidistant, so we extend the linear fit out to slots that
        # don't have a hero detection yet.
        hero_blocks.sort(key=lambda b: _block_center_y(b.bbox))
        hero_ys = [_block_center_y(b.bbox) for b in hero_blocks]
        # Find the 5-slot grid that best matches hero ys: try fitting a
        # 5-step linear grid and see which slot indices the heroes
        # occupy.
        slot_ys = _infer_5_slot_grid(hero_ys)
        slot_ys = [y + _PLAYER_BELOW_HERO for y in slot_ys]
    elif non_hero_blocks:
        # Fallback: not enough hero anchors but we *do* have some
        # player-name detections. Snap each one to the closest canonical
        # slot Y (``_SLOT_Y_CENTERS`` is the empirical 5-row grid for the
        # BP UI), keeping at most one per slot. This handles BP's earliest
        # phase — no heroes locked, only a couple of players' names
        # detected — without misallocating those names to slots 0/1.
        slot_ys = list(_SLOT_Y_CENTERS)
        slot_pick: list[OcrBlock | None] = [None] * 5
        for b in non_hero_blocks:
            cy = _block_center_y(b.bbox)
            idx = min(range(5), key=lambda i: abs(cy - slot_ys[i]))
            if abs(cy - slot_ys[idx]) > _SLOT_Y_TOLERANCE:
                continue
            existing = slot_pick[idx]
            if existing is None or b.confidence > existing.confidence:
                slot_pick[idx] = b
        names = [(b.text.strip() if b is not None else "") for b in slot_pick]
        confs = [(float(b.confidence) if b is not None else 0.0) for b in slot_pick]
        return names, confs, list(slot_ys)
    else:
        slot_ys = list(_SLOT_Y_CENTERS)

    # Step C: snap each non-hero block to the nearest slot.
    slot_choice: list[OcrBlock | None] = [None] * 5
    for b in non_hero_blocks:
        cy = _block_center_y(b.bbox)
        # Find the closest slot.
        idx = min(range(5), key=lambda i: abs(cy - slot_ys[i]))
        if abs(cy - slot_ys[idx]) > _SLOT_Y_TOLERANCE:
            continue
        existing = slot_choice[idx]
        if existing is None or b.confidence > existing.confidence:
            slot_choice[idx] = b

    names = [(b.text.strip() if b is not None else "") for b in slot_choice]
    confs = [(float(b.confidence) if b is not None else 0.0) for b in slot_choice]
    return names, confs, list(slot_ys)


def _infer_5_slot_grid(hero_ys: list[float]) -> list[float]:
    """Given some hero-caption y centers, return the 5 player-slot ys.

    Uses the smallest gap between adjacent heroes as the slot pitch and
    extends the grid up/down to cover 5 slots. Robust to partially-drafted
    states where only 2-4 heroes have been picked.
    """
    if not hero_ys:
        return list(_SLOT_Y_CENTERS)
    if len(hero_ys) == 1:
        # Anchor at the single detected hero, assume centered.
        gap = (_SLOT_Y_CENTERS[-1] - _SLOT_Y_CENTERS[0]) / 4
        idx = min(range(5), key=lambda i: abs(_SLOT_Y_CENTERS[i] - hero_ys[0]))
        base = hero_ys[0] - idx * gap
        return [base + i * gap for i in range(5)]

    # 2+ heroes: figure out which slot indices they belong to by trying
    # all valid (first_slot, last_slot) assignments and picking the
    # lowest residual. Each assignment fixes a (base, gap) pair that
    # we then evaluate against:
    #   - how well *all* detected hero ys snap onto integer slots, AND
    #   - how close the implied grid sits to the canonical slot ys
    #     (``_SLOT_Y_CENTERS``), so a degenerate "two heroes on slots
    #     0 and 1" assignment loses to "two heroes on slots 3 and 4"
    #     when the actual heroes were detected near the bottom.
    canonical = list(_SLOT_Y_CENTERS)
    best: tuple[float, list[float]] = (float("inf"), canonical)
    for assign_first in range(5):
        for assign_last in range(assign_first + 1, 5):
            steps = assign_last - assign_first
            gap = (hero_ys[-1] - hero_ys[0]) / steps
            base = hero_ys[0] - assign_first * gap
            grid = [base + i * gap for i in range(5)]
            res = 0.0
            for hy in hero_ys:
                slot_idx = min(range(5), key=lambda i: abs(grid[i] - hy))
                res += (grid[slot_idx] - hy) ** 2
            if not (0.10 < gap < 0.20):
                res += 0.5
            # Anchor against canonical positions: if the inferred grid
            # has slot0 at 0.65 it's wildly off (real top hero is at
            # ~0.20). Add a quadratic penalty proportional to how far
            # the whole grid drifted from canonical.
            for i in range(5):
                res += 0.5 * (grid[i] - canonical[i]) ** 2
            if res < best[0]:
                best = (res, grid)
    return best[1]


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


# --- per-column multi-angle OCR --------------------------------------------
#
# The BP UI tilts each side's player-name strip ~12-18° from horizontal,
# and the topmost slot on each column tilts even more (the hex stack
# isn't perfectly symmetric). A single OCR pass over the whole
# screenshot at the original orientation reliably misses 1-2 of the
# slanted strips per side. Running OCR on each side's cropped column
# at 0° + a couple of rotation angles, then merging the per-block
# results back to slot positions, recovers those misses without
# requiring a server-grade OCR model. ~2-3× the OCR wall-clock of a
# single full-image pass; still faster than the previous crop
# fallback because we drop that path when this one runs.

# X bands of each side's column. Generous enough to catch the
# rotated strip without including the centre stage (bans / drafter).
_LEFT_COLUMN_X = (0.0, 0.20)
_RIGHT_COLUMN_X = (0.80, 1.0)

# Rotation angles to try per column. The mirror side (right vs left)
# swaps signs because the UI is mirrored. Empirically:
#   * 0°  catches the middle slots (2/3) where the strip is roughly
#         horizontal,
#   * +12° catches the bottom-of-column slants,
#   * +15° catches the very top (slot 0) where the slant is steepest.
# Angles applied to the LEFT column; the RIGHT column uses the negated
# values automatically. A handful of mirrored angles also helps when
# the column happens to be flipped on a non-standard aspect ratio.
# Empirically measured from RapidOCR's 4-point detection polygons on
# real BP screenshots (1728×1117): the left column's text baselines
# tilt at ~+30° (right-end up), the right column's at ~-30° (left-end
# up). The hex stack is symmetric so the two values are mirror images.
# We rotate the column crop by the *negative* of those angles to
# straighten the text before OCR — i.e. left column gets rotated
# CW (negative) by 30°, right column gets rotated CCW (positive) by 30°.
#
# Why a small spread of angles around the canonical -30° / +30°:
# the slot-0 strips on each column tilt slightly more (~33-35°)
# because of how the hex stack flares out, and the avatar's hex
# border itself injects ~3° of jitter. Three angles per side is
# enough to cover both the steep top slot and the moderate
# middle/bottom ones without doubling the OCR cost.
_COLUMN_ROT_ANGLES_LEFT = (+30, +33, +27)
_COLUMN_ROT_ANGLES_RIGHT = (-30, -33, -27)

# Confidence floor when accepting a multi-angle block as a player name.
_COLUMN_BLOCK_CONF_FLOOR = 0.70

# Slot tolerance for snapping a multi-angle block to a slot Y.
_COLUMN_SLOT_TOLERANCE = 0.06


def _rotate_with_inverse(crop, angle):
    """Rotate ``crop`` by ``angle`` and return ``(rotated, to_orig_xy)``.

    ``to_orig_xy(rx, ry)`` maps a coordinate inside the rotated image
    back to ``(crop_x, crop_y)`` in the un-rotated crop. We need this
    so detected text bboxes can be projected onto the original
    screenshot's slot grid.

    Sign conventions (the bit that's easy to get wrong):

    * Both PIL's ``Image.rotate`` and standard 2D rotation matrices
      are *counter-clockwise positive*. PIL also uses image-y-down,
      which flips the visual sense — a positive ``angle`` rotates
      the image visually clockwise.
    * To invert ``Image.rotate(angle)`` we rotate any forward
      coordinate by **+angle**, around the *rotated* image centre,
      shifted to the *original* image centre. ``cos a / sin a`` are
      derived directly from ``angle`` (NOT ``-angle``) — this was the
      previous bug: I'd negated angle once for the inverse, but PIL
      and numpy both store angle CCW-positive, so the right inverse
      uses ``+angle``.
    """
    from math import cos, sin, radians
    if angle == 0 or abs(angle) < 1e-3:
        def _to_orig_xy(rx, ry):
            return rx, ry
        return crop, _to_orig_xy
    try:
        from PIL import Image
    except ImportError:
        def _to_orig_xy(rx, ry):
            return rx, ry
        return crop, _to_orig_xy
    rotated = crop.rotate(angle, expand=True, fillcolor=(0, 0, 0))
    cx_old = crop.width / 2
    cy_old = crop.height / 2
    cx_new = rotated.width / 2
    cy_new = rotated.height / 2
    # Forward rotation by ``angle`` = CCW by ``angle``. Inverse =
    # CCW by ``-angle``. Image-y-down adds a sign flip for the y
    # component, which folds into the sin entries below.
    a = radians(angle)
    cos_a, sin_a = cos(a), sin(a)

    def _to_orig_xy(rx: float, ry: float) -> tuple[float, float]:
        # Translate to rotated-image centre, apply the inverse 2D
        # rotation, translate to un-rotated-crop centre. Image-y-down
        # plus PIL's CCW-positive ``angle`` give this matrix
        # (verified empirically against known landmark coords):
        #   [ cos a   sin a ]
        #   [ sin a   cos a ]
        # i.e. the y row uses **+sin a**, not -sin a — that was the
        # earlier bug that pushed every back-projected cy off by ~30°
        # of the column height.
        dx = rx - cx_new
        dy = ry - cy_new
        ox = cos_a * dx + sin_a * dy + cx_old
        oy = sin_a * dx + cos_a * dy + cy_old
        return ox, oy
    return rotated, _to_orig_xy


def _ocr_column_image(crop, languages: list[str] | None) -> list[OcrBlock]:
    """Run RapidOCR over an in-memory PIL crop and return OcrBlock list.

    Coordinates are normalised to the *crop's* dimensions, not the
    original screenshot.
    """
    try:
        return recognize_array(crop, languages=languages)
    except Exception:
        return []


# Slot Y centres on the **rotated column** image. The five hex slots
# alternate left/right, which makes their player-name strips fall on a
# zigzag — every other gap is twice the size of the small one. The
# pattern is [0, 1, 3, 4, 6] × unit, with the unit measured against
# real screenshots and slot 0 anchored at 0.30. (Verified against
# both the 1728×1117 Mac screenshots and the 2276×1280 / 3840×2160
# Windows ones — the rotated layout is identical because we trim
# the black band off all of them before measuring.)
_ROTATED_SLOT_Y_BASE = 0.30
_ROTATED_SLOT_Y_UNIT = 0.089
_ROTATED_SLOT_STEPS = (0, 1, 3, 4, 6)
_ROTATED_SLOT_YS = tuple(
    _ROTATED_SLOT_Y_BASE + _ROTATED_SLOT_Y_UNIT * step
    for step in _ROTATED_SLOT_STEPS
)
# How tall the per-slot crop is, in normalised rotated-image coords.
# 0.044 (≈ 50 px on a 1139-px-tall rotated column) is large enough to
# tolerate the slot-Y measurement drift seen across screenshots while
# keeping the hero caption above and the next slot below out of the
# crop on most layouts.
_ROTATED_SLOT_HALF_H = 0.044
# Y offset to nudge the canonical strip Y a little upwards. The empirical
# data has the actual player-name strip at y ≈ slot_y - 0.013 on Mac
# screenshots — anchoring on the hex centre rather than the strip itself.
_ROTATED_SLOT_Y_OFFSET = -0.013

# Per-slot **X** centres on the rotated column. The hex avatars on
# each side alternate left-right-left-right-left, which puts each
# slot's player-name strip at a different cx on the rotated image.
# Cropping just a window centred on these cx values (instead of the
# full row width) is what finally lets the OCR detector see the
# top-of-column slots: with an entire 858-px row, ``공대장`` is a
# tiny strip in the corner and the detector misses it; with a
# 0.45-wide window centred on cx=0.82, ``공대장`` occupies a clearly
# detectable share of the input.
#
# Values measured directly from RapidOCR detections across 6 real
# screenshots (4 Mac at 1728×1117, 1 Windows at 2276×1280, 1 Windows
# at 3840×2160). Drift across resolutions is < ±0.02; the half-width
# below absorbs that.
_ROTATED_LEFT_SLOT_XS = (0.18, 0.37, 0.36, 0.57, 0.56)
_ROTATED_RIGHT_SLOT_XS = (0.82, 0.63, 0.63, 0.42, 0.44)
# Half the width of each per-slot crop, in normalised rotated-image
# coords. 0.22 (≈ 190 px on an 858-px-wide rotated column) is wide
# enough to fit the longest player names we've seen
# (``대명호반의용목목`` etc., 7+ glyphs) plus a small margin so the
# detector's text-box framing has room to breathe.
_ROTATED_SLOT_HALF_W = 0.22


def _per_slot_rotated_ocr(
    image_path: Path,
    *,
    side: str,
    languages: list[str] | None,
    only_slots: set[int] | None = None,
) -> list[tuple[str, float]]:
    """For each of the 5 slots on ``side``, rotate the column to
    horizontal, crop a tight strip around the predicted player-name
    Y, sharpen + autocontrast + upscale, and OCR.

    Returns a list ``[(name, conf)] × 5``. Slots that don't OCR
    cleanly come back as ``("", 0.0)``.

    The rotation step is the only step that really matters here:
    PaddleOCR's recogniser is trained on horizontal text, so feeding
    it a slanted strip drops accuracy by 20-30 pp. The per-slot crop
    + 3× upscale on top of the rotation lifts the rec model's input
    from ~30 px tall (where it would normally interpolate down to 48
    px and lose detail) to ~180 px tall (where it interpolates *up*
    and keeps every stroke).
    """
    try:
        from PIL import Image, ImageOps, ImageFilter
    except ImportError:
        return [("", 0.0)] * 5

    x_lo, x_hi = _LEFT_COLUMN_X if side == "L" else _RIGHT_COLUMN_X
    rotate_by = +30 if side == "L" else -30

    out: list[tuple[str, float]] = []
    with Image.open(image_path) as im:
        W, H = im.size
        col_x0 = int(x_lo * W)
        col_x1 = int(x_hi * W)
        if col_x1 <= col_x0:
            return [("", 0.0)] * 5
        column = im.crop((col_x0, 0, col_x1, H))
        rotated = column.rotate(rotate_by, expand=True, fillcolor=(0, 0, 0))
        bbox = rotated.getbbox()
        if bbox is not None:
            rotated = rotated.crop(bbox)
        rW, rH = rotated.size

        slot_xs = (
            _ROTATED_LEFT_SLOT_XS if side == "L" else _ROTATED_RIGHT_SLOT_XS
        )
        for slot_i, slot_y_n in enumerate(_ROTATED_SLOT_YS):
            if only_slots is not None and slot_i not in only_slots:
                out.append(("", 0.0))
                continue
            cy_n = slot_y_n + _ROTATED_SLOT_Y_OFFSET
            cy_px = int(cy_n * rH)
            half_h_px = int(_ROTATED_SLOT_HALF_H * rH)
            y0 = max(0, cy_px - half_h_px)
            y1 = min(rH, cy_px + half_h_px)
            cx_n = slot_xs[slot_i]
            cx_px = int(cx_n * rW)
            half_w_px = int(_ROTATED_SLOT_HALF_W * rW)
            x0 = max(0, cx_px - half_w_px)
            x1 = min(rW, cx_px + half_w_px)
            if y1 <= y0 or x1 <= x0:
                out.append(("", 0.0))
                continue
            slot_crop = rotated.crop((x0, y0, x1, y1))

            # Preprocess: grey + autocontrast + 3× LANCZOS upscale +
            # mild unsharp mask. No invert — the player-name strip is
            # already light text on a dim background, which matches
            # PaddleOCR's training distribution.
            proc = slot_crop.convert("L")
            proc = ImageOps.autocontrast(proc, cutoff=2)
            proc = proc.resize(
                (proc.width * 3, proc.height * 3),
                resample=Image.LANCZOS,
            )
            try:
                proc = proc.filter(
                    ImageFilter.UnsharpMask(radius=1.5, percent=200)
                )
            except Exception:
                pass

            # Run OCR on the processed strip. We don't go through the
            # heavy multi-rotation rescue path (the strip is already
            # horizontal — extra rotations just blur it further); a
            # single OCR pass with the configured language set is
            # enough.
            blocks = _ocr_pil_blocks(proc, languages=languages)
            best = _select_best_player_block(blocks)
            if best is None:
                out.append(("", 0.0))
                continue
            out.append(best)
    return out


def _column_rotated_blocks(
    image_path: Path,
    *,
    side: str,
    languages: list[str] | None,
) -> list[OcrBlock]:
    """Crop a side column, rotate it back to horizontal, OCR the
    straightened strip, project results back to full-image coords.

    Empirically the player-name strips tilt **+30° on the left
    column** and **-30° on the right column** (measured from the
    OCR detector's 4-point polygon angles on real screenshots). We
    rotate by the *negative* of the strip tilt so the text becomes
    horizontal in the rotated image — that's the orientation the
    PaddleOCR detector + recogniser were trained on, and it's
    where they hit the highest accuracy.
    """
    try:
        from PIL import Image
    except ImportError:
        return []

    x_lo, x_hi = _LEFT_COLUMN_X if side == "L" else _RIGHT_COLUMN_X
    rotate_by = +30 if side == "L" else -30

    out: list[OcrBlock] = []
    with Image.open(image_path) as im:
        W, H = im.size
        col_x0 = int(x_lo * W)
        col_x1 = int(x_hi * W)
        if col_x1 <= col_x0:
            return []
        column = im.crop((col_x0, 0, col_x1, H))
        rotated, to_crop_xy = _rotate_with_inverse(column, rotate_by)
        # Auto-trim the surrounding black band PIL added when expanding
        # the canvas — it's noise that confuses RapidOCR's det model
        # and blows up image size for the rec stage.
        bbox = rotated.getbbox()
        crop_offset_x = 0
        crop_offset_y = 0
        if bbox is not None:
            crop_offset_x, crop_offset_y = bbox[0], bbox[1]
            rotated = rotated.crop(bbox)
        for raw_block in _ocr_column_image(rotated, languages=languages):
            rx0 = raw_block.bbox[0] * rotated.width + crop_offset_x
            ry0 = raw_block.bbox[1] * rotated.height + crop_offset_y
            rx1 = raw_block.bbox[2] * rotated.width + crop_offset_x
            ry1 = raw_block.bbox[3] * rotated.height + crop_offset_y
            xs, ys = [], []
            for rx, ry in ((rx0, ry0), (rx1, ry0), (rx0, ry1), (rx1, ry1)):
                cx, cy = to_crop_xy(rx, ry)
                xs.append(cx)
                ys.append(cy)
            cx0 = max(0.0, min(xs))
            cy0 = max(0.0, min(ys))
            cx1 = min(float(column.width), max(xs))
            cy1 = min(float(column.height), max(ys))
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            fx0 = (col_x0 + cx0) / W
            fy0 = cy0 / H
            fx1 = (col_x0 + cx1) / W
            fy1 = cy1 / H
            out.append(OcrBlock(
                text=raw_block.text,
                bbox=(fx0, fy0, fx1, fy1),
                confidence=raw_block.confidence,
            ))
    return out


def _column_multiangle_blocks(
    image_path: Path,
    *,
    side: str,
    languages: list[str] | None,
) -> list[OcrBlock]:
    """OCR a single column at multiple rotation angles, return blocks
    in *full-screenshot* normalised coordinates.

    Each detected block is emitted once per (angle, language) pass.
    The merge step in :func:`_assign_blocks_to_slots` keeps the
    highest-confidence block per slot.
    """
    try:
        from PIL import Image
    except ImportError:
        return []

    x_lo, x_hi = _LEFT_COLUMN_X if side == "L" else _RIGHT_COLUMN_X
    angles = (
        _COLUMN_ROT_ANGLES_LEFT if side == "L" else _COLUMN_ROT_ANGLES_RIGHT
    )

    out: list[OcrBlock] = []
    with Image.open(image_path) as im:
        W, H = im.size
        col_x0 = int(x_lo * W)
        col_x1 = int(x_hi * W)
        if col_x1 <= col_x0:
            return []
        column = im.crop((col_x0, 0, col_x1, H))
        col_w, col_h = column.size

        for angle in angles:
            rotated, to_crop_xy = _rotate_with_inverse(column, angle)
            blocks = _ocr_column_image(rotated, languages=languages)
            if not blocks:
                continue
            rot_w = float(rotated.width)
            rot_h = float(rotated.height)
            for b in blocks:
                # b.bbox is normalised to the rotated image; project
                # back to the un-rotated column, then to the full
                # screenshot.
                rx0 = b.bbox[0] * rot_w
                ry0 = b.bbox[1] * rot_h
                rx1 = b.bbox[2] * rot_w
                ry1 = b.bbox[3] * rot_h
                # Map all four corners and take their bounding box —
                # rotation makes a rectangle into a parallelogram.
                xs = []
                ys = []
                for rx, ry in (
                    (rx0, ry0), (rx1, ry0), (rx0, ry1), (rx1, ry1)
                ):
                    cx, cy = to_crop_xy(rx, ry)
                    xs.append(cx)
                    ys.append(cy)
                cx0 = max(0.0, min(xs))
                cy0 = max(0.0, min(ys))
                cx1 = min(float(col_w), max(xs))
                cy1 = min(float(col_h), max(ys))
                if cx1 <= cx0 or cy1 <= cy0:
                    continue
                # Translate column-local pixels to full-screenshot
                # normalised coords.
                fx0 = (col_x0 + cx0) / W
                fy0 = cy0 / H
                fx1 = (col_x0 + cx1) / W
                fy1 = cy1 / H
                out.append(OcrBlock(
                    text=b.text,
                    bbox=(fx0, fy0, fx1, fy1),
                    confidence=b.confidence,
                ))
    return out


def _assign_column_blocks_to_slots(
    blocks: list[OcrBlock],
    slot_ys: list[float],
) -> tuple[list[str], list[float]]:
    """Snap each OCR block to the nearest slot Y (within tolerance).

    Multiple blocks per slot: the highest-confidence non-hero,
    non-UI-chrome block wins. Empty slots stay empty.
    """
    slot_choice: list[OcrBlock | None] = [None] * 5
    for b in blocks:
        text = b.text.strip()
        if not text or len(text) < 2:
            continue
        if _is_probably_ui_chrome(text):
            continue
        try:
            from .heroes import is_hero_name  # noqa: F401  (lazy import)
        except ImportError:
            pass
        else:
            if is_hero_name(text):
                continue
        if b.confidence < _COLUMN_BLOCK_CONF_FLOOR:
            continue
        cy = _block_center_y(b.bbox)
        idx = min(range(5), key=lambda i: abs(cy - slot_ys[i]))
        if abs(cy - slot_ys[idx]) > _COLUMN_SLOT_TOLERANCE:
            continue
        existing = slot_choice[idx]
        if existing is None or b.confidence > existing.confidence:
            slot_choice[idx] = b

    names = [(b.text.strip() if b is not None else "") for b in slot_choice]
    confs = [(float(b.confidence) if b is not None else 0.0) for b in slot_choice]
    return names, confs


def _merge_side_results(
    primary_names: list[str], primary_confs: list[float],
    extra_names: list[str], extra_confs: list[float],
) -> tuple[list[str], list[float]]:
    """Pick the better of two parallel slot reads per slot index.

    Used to merge the multi-angle column read into the full-image
    read. The merge logic:

    * Empty primary → use extra.
    * Both Latin (ASCII) **and** very similar (edit distance ≤ 2 / 25%
      of the longer): trust the *primary* (full-image cn+en is more
      accurate on Latin). This avoids the "BeigeBison → BelgeBison"
      regression where the rotated KR rec misreads one Latin glyph.
    * Otherwise: take the higher-confidence read. The rotated pass
      is the only one that catches CJK strips on slanted columns,
      so we let it win when it has signal the primary lacks.
    """
    out_names = list(primary_names)
    out_confs = list(primary_confs)
    for i in range(min(len(out_names), len(extra_names))):
        ex_name = extra_names[i]
        ex_conf = extra_confs[i]
        if not ex_name:
            continue
        cur_name = out_names[i]
        cur_conf = out_confs[i]
        if not cur_name:
            out_names[i] = ex_name
            out_confs[i] = ex_conf
            continue
        # Both non-empty. If both are Latin and look like the same
        # name modulo OCR drift, keep the primary (cn+en is the
        # better Latin recogniser).
        if cur_name.isascii() and ex_name.isascii():
            if _ascii_close_enough(cur_name, ex_name):
                continue
        if ex_conf > cur_conf:
            out_names[i] = ex_name
            out_confs[i] = ex_conf
    return out_names, out_confs


def _ascii_close_enough(a: str, b: str) -> bool:
    """``True`` when two ASCII strings are within the OCR-drift band
    (case-insensitive, edit distance ≤ max(2, 25 % of the longer
    string)).
    """
    if not a or not b:
        return False
    al, bl = a.lower(), b.lower()
    if al == bl:
        return True
    longer = max(len(al), len(bl))
    threshold = max(2, longer // 4)
    # Cheap edit distance — same impl style the project already uses
    # in the player-name fuzzy resolver. Only worth running for short
    # strings, which player names always are.
    if abs(len(al) - len(bl)) > threshold:
        return False
    prev = list(range(len(bl) + 1))
    for i, ca in enumerate(al, 1):
        cur = [i] + [0] * len(bl)
        for j, cb in enumerate(bl, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j-1] + 1, prev[j-1] + cost)
        prev = cur
    return prev[len(bl)] <= threshold


def _center_band_blocks(
    image_path: Path,
    *,
    languages: list[str] | None,
) -> list[OcrBlock]:
    """OCR just the centre band of the screenshot — the strip that
    holds the map name (top centre) and the drafter spotlight (~middle).

    The two columns are picked up by :func:`_column_rotated_blocks`
    with rotation correction, so this pass deliberately stays clear
    of the player-name strips. Returns blocks in *full-screenshot*
    normalised coords.
    """
    try:
        from PIL import Image
    except ImportError:
        return []

    out: list[OcrBlock] = []
    with Image.open(image_path) as im:
        W, H = im.size
        x0 = int(0.20 * W)
        x1 = int(0.80 * W)
        # The map name lives at cy ~0.05, the drafter banner at
        # cy ~0.5. Crop a generous middle band that catches both.
        y0 = 0
        y1 = int(0.65 * H)
        if x1 <= x0 or y1 <= y0:
            return []
        crop = im.crop((x0, y0, x1, y1))
        for raw in _ocr_column_image(crop, languages=languages):
            cw, ch = float(crop.width), float(crop.height)
            cx0 = raw.bbox[0] * cw
            cy0 = raw.bbox[1] * ch
            cx1 = raw.bbox[2] * cw
            cy1 = raw.bbox[3] * ch
            fx0 = (x0 + cx0) / W
            fy0 = (y0 + cy0) / H
            fx1 = (x0 + cx1) / W
            fy1 = (y0 + cy1) / H
            out.append(OcrBlock(
                text=raw.text,
                bbox=(fx0, fy0, fx1, fy1),
                confidence=raw.confidence,
            ))
    return out


def parse_screenshot(
    image_path: Path,
    *,
    blocks: list[OcrBlock] | None = None,
    languages: list[str] | None = None,
) -> ParsedScreenshot:
    """Full screenshot → (map, ally_names, enemy_names, drafter) + confidences.

    Three-region pipeline (no full-image OCR):

    * **Centre band** (x ∈ [0.20, 0.80], y ∈ [0, 0.65]) → map name
      + drafter spotlight via :func:`_center_band_blocks`.
    * **Left column** (x ∈ [0, 0.20]) → ally names. Rotated by +30°
      so the slanted player-name strips become horizontal, then
      OCR'd via :func:`_column_rotated_blocks`. Detected blocks
      are snapped to the canonical 5-slot grid (``_SLOT_Y_CENTERS``).
    * **Right column** (x ∈ [0.80, 1.00]) → enemy names. Same
      pipeline, rotated by -30°.

    Hero-name captions still appear on the rotated columns (each
    avatar has the hero name printed below it), but the player-name
    block-picker drops them via :func:`heroes.is_hero_name` before
    any name lands in a slot. We no longer use hero detections as
    Y anchors; the canonical slot grid is stable enough on the
    rotated crop that we can snap directly to it.

    The ``blocks`` argument is accepted for API compatibility but
    ignored — this pipeline runs its own OCR on each region, and
    a stale full-image block list would just compete with the
    fresh per-region reads.
    """
    if blocks is None:
        blocks = recognize(image_path, languages=languages)
    if not blocks:
        return ParsedScreenshot(
            map_name="", ally_names=[""] * 5, enemy_names=[""] * 5,
            map_confidence=0.0,
            ally_confidences=[0.0] * 5,
            enemy_confidences=[0.0] * 5,
            drafter="",
        )

    # 1. Full-image pass for the centre band (map + drafter) and
    # whatever player names the un-rotated detector can pick up. The
    # full-image pass with cn+en is significantly more accurate on
    # Latin player names (``BeigeBison`` vs the KR rec's
    # ``BelgeBison``-style misreads), so we keep it as the primary
    # source and let the rotated column pass fill in the gaps.
    map_name, map_conf = _pick_map(blocks)
    ally_names, ally_confs, ally_ys = _pick_side(blocks, "L")
    enemy_names, enemy_confs, enemy_ys = _pick_side(blocks, "R")
    drafter = _pick_drafter(blocks)

    # 2. Per-column rotation rescue. Each side's player-name strip
    # tilts at ±30° (left +30°, right -30°). We crop each column,
    # rotate it back to horizontal, and OCR the straightened strip.
    # This recovers the slanted KR strips (especially the topmost
    # slot 0 and the column-bottom slots) the full-image pass keeps
    # missing. Always run; the merge step below only overwrites
    # full-image reads with strictly higher-confidence rotated
    # reads, so it's safe to always do this work.
    try:
        ally_extra_blocks = _column_rotated_blocks(
            image_path, side="L", languages=languages,
        )
        ally_extra_names, ally_extra_confs = _assign_column_blocks_to_slots(
            ally_extra_blocks, ally_ys,
        )
        ally_names, ally_confs = _merge_side_results(
            ally_names, ally_confs, ally_extra_names, ally_extra_confs,
        )
    except Exception:
        pass
    try:
        enemy_extra_blocks = _column_rotated_blocks(
            image_path, side="R", languages=languages,
        )
        enemy_extra_names, enemy_extra_confs = _assign_column_blocks_to_slots(
            enemy_extra_blocks, enemy_ys,
        )
        enemy_names, enemy_confs = _merge_side_results(
            enemy_names, enemy_confs, enemy_extra_names, enemy_extra_confs,
        )
    except Exception:
        pass

    # 3. Per-slot precision rescue on the rotated columns, but only
    # for slots that look suspect (empty or low-confidence). For each
    # such slot, crop a tight horizontal strip out of the rotated
    # column at the slot's predicted Y, upscale 3× + sharpen, and
    # re-OCR. This is what cracks the column-3/4 slanted KR strips
    # without spending OCR cycles on slots that are already solid.
    _SUSPECT_CONF = 0.90
    try:
        ally_suspect = {
            i for i in range(5)
            if not ally_names[i] or ally_confs[i] < _SUSPECT_CONF
        }
        if ally_suspect:
            ally_per_slot = _per_slot_rotated_ocr(
                image_path, side="L", languages=languages,
                only_slots=ally_suspect,
            )
            ally_extra_names = [t for t, _ in ally_per_slot]
            ally_extra_confs = [c for _, c in ally_per_slot]
            ally_names, ally_confs = _merge_side_results(
                ally_names, ally_confs, ally_extra_names, ally_extra_confs,
            )
        enemy_suspect = {
            i for i in range(5)
            if not enemy_names[i] or enemy_confs[i] < _SUSPECT_CONF
        }
        if enemy_suspect:
            enemy_per_slot = _per_slot_rotated_ocr(
                image_path, side="R", languages=languages,
                only_slots=enemy_suspect,
            )
            enemy_extra_names = [t for t, _ in enemy_per_slot]
            enemy_extra_confs = [c for _, c in enemy_per_slot]
            enemy_names, enemy_confs = _merge_side_results(
                enemy_names, enemy_confs, enemy_extra_names, enemy_extra_confs,
            )
    except Exception:
        pass

    # 4. Last-resort crop fallback (un-rotated tight crop). Runs only
    # for slots that are still empty after the rotated pass — the
    # un-rotated path uses different geometry and occasionally
    # catches strips the rotated pass's slot-Y model puts a few
    # pixels off.
    try:
        ally_names, ally_confs = _crop_fallback(
            image_path, ally_names, ally_confs, ally_ys,
            side="L", languages=languages,
        )
        enemy_names, enemy_confs = _crop_fallback(
            image_path, enemy_names, enemy_confs, enemy_ys,
            side="R", languages=languages,
        )
    except Exception:
        pass

    return ParsedScreenshot(
        map_name=map_name,
        ally_names=ally_names,
        enemy_names=enemy_names,
        map_confidence=map_conf,
        ally_confidences=ally_confs,
        enemy_confidences=enemy_confs,
        drafter=drafter,
    )


# --- per-slot crop fallback --------------------------------------------------

# X bands for the player-name strip on each side. Wider than the avatar
# itself so the crop catches names longer than the avatar's hex width.
_LEFT_CROP_X = (0.005, 0.165)
_RIGHT_CROP_X = (0.78, 0.995)
# How much of the slot's vertical pitch the name strip occupies. Tall
# enough to catch the slanted KR name strips on the right column without
# clipping their last glyph; the multi-block selection in
# :func:`_pick_player_block_from_crop` picks the right text out of the
# crop when both hero caption and player name end up inside.
_CROP_HALF_HEIGHT = 0.030
# Player-name strip y offset from canonical slot center.
# ``_SLOT_Y_CENTERS`` tracks the *hero name caption* y positions
# (top to bottom: 0.20, 0.36, 0.52, 0.67, 0.82). The player-name
# strip sits ~0.028 below each hero caption — so a crop centred on
# the canonical slot would scrape both the hero caption and the
# player name. Add this offset to crop only the player strip.
_PLAYER_STRIP_Y_OFFSET = 0.028
# Upscale factor for the crop before re-OCR. 2× lifts confidence on the
# small KR/JP strokes without exploding inference time.
_CROP_UPSCALE = 2
# Slots whose full-image OCR confidence falls below this floor are
# treated as suspect and get re-read via the per-slot crop pipeline.
# Real player-name reads usually clock 0.95+ on the v5 model; anything
# in the 0.5-0.85 band is empirically a slanted-strip mis-read like
# ``"10品品暑尼比君"``.
_CROP_CONFIDENCE_FLOOR = 0.85


def _crop_fallback(
    image_path: Path,
    names: list[str],
    confs: list[float],
    slot_ys: list[float],
    *,
    side: str,
    languages: list[str] | None,
) -> tuple[list[str], list[float]]:
    """Re-OCR each *suspect* slot from a tight crop around its expected y.

    A slot is suspect when:

    * it's empty (full-image OCR detector missed the strip), or
    * its confidence is below ``_CROP_CONFIDENCE_FLOOR`` (full-image
      OCR returned a low-confidence guess that's almost always
      garbage on the slanted KR strips, e.g. ``"10品品暑尼比君"``).

    ``slot_ys`` carries the inferred player-name y for each slot
    (``_pick_side`` returns it). When that y is unknown (slot was
    never anchored — e.g. fewer than 2 hero detections on the side)
    we fall back to ``_SLOT_Y_CENTERS`` + the canonical hero→player
    offset.

    The crop result only overwrites the existing name when it's
    *better* — non-empty and either replacing an empty slot or
    landing at a higher confidence than the original. Otherwise the
    original (low-conf) read stays put.
    """
    suspects = [
        i for i in range(len(names))
        if not names[i] or confs[i] < _CROP_CONFIDENCE_FLOOR
    ]
    if not suspects:
        return names, confs
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return names, confs

    x_lo, x_hi = _LEFT_CROP_X if side == "L" else _RIGHT_CROP_X
    out_names = list(names)
    out_confs = list(confs)
    with Image.open(image_path) as im:
        W, H = im.size
        for i in suspects:
            cy = slot_ys[i] if (i < len(slot_ys) and slot_ys[i] > 0) \
                else (_SLOT_Y_CENTERS[i] + _PLAYER_STRIP_Y_OFFSET)
            y0 = max(0, int((cy - _CROP_HALF_HEIGHT) * H))
            y1 = min(H, int((cy + _CROP_HALF_HEIGHT) * H))
            x0 = int(x_lo * W)
            x1 = int(x_hi * W)
            if x1 <= x0 or y1 <= y0:
                continue
            crop = im.crop((x0, y0, x1, y1))
            # Predominantly dark BP background — invert + autocontrast +
            # grayscale gives the OCR detector the high-contrast input
            # it expects. Most player-name strips are bright text on
            # dim background, so inverting first lets autocontrast
            # stretch the *text* range to full dynamic range.
            crop = crop.convert("L")
            crop = ImageOps.invert(crop)
            crop = ImageOps.autocontrast(crop, cutoff=2)
            if _CROP_UPSCALE != 1:
                crop = crop.resize(
                    (crop.width * _CROP_UPSCALE, crop.height * _CROP_UPSCALE),
                    resample=Image.LANCZOS,
                )
            text, conf = _pick_player_block_from_crop(
                crop, languages=languages,
            )
            if not text:
                continue
            # Only overwrite when the crop result is strictly better:
            # either the slot was empty, or the new confidence beats
            # the existing low-conf garbage.
            if not out_names[i] or conf > out_confs[i]:
                out_names[i] = text
                out_confs[i] = conf
    return out_names, out_confs


def _pick_player_block_from_crop(
    crop, *, languages: list[str] | None,
) -> tuple[str, float]:
    """Run OCR on the crop and return the best player-name block.

    Per-slot crops on the right column routinely contain *both* the
    hero caption and the player-name strip because the strips are
    slanted ~12° and a tall enough crop to catch a Korean glyph also
    catches the hero text above it. We can't shrink the crop further
    without losing the player name, so instead we OCR the whole crop,
    then pick the block that:

    * isn't a hero name (or hero-name prefix),
    * isn't UI chrome,
    * is at least 2 chars long,
    * is the longest such block (real player names are usually
      longer than the OCR debris that sometimes appears alongside
      avatar borders),
    * with confidence as the final tiebreak.

    Falls back through several rescue passes — a sharpened version
    of the original crop, then a few small rotations — to recover
    KR strips that read as garbage (`10品品暑尼比君`) at the
    detector's slant.
    """
    # Pass 1: original orientation. Quick out when this works.
    candidates = _ocr_pil_blocks(crop, languages=languages)
    pick = _select_best_player_block(candidates)
    best: tuple[str, float] | None = pick

    # Pass 2: sharpened. Slanted KR strips often have soft, anti-
    # aliased glyph edges that the rec model reads as wrong-character
    # noise. An unsharp mask before OCR sharpens stroke contrast,
    # which sometimes flips a 0.7-confidence garbage read into the
    # right name.
    sharpened = _sharpen(crop)
    if sharpened is not None:
        candidates = _ocr_pil_blocks(sharpened, languages=languages)
        sharpened_pick = _select_best_player_block(candidates)
        if sharpened_pick is not None and (
            best is None or sharpened_pick[1] > best[1]
        ):
            best = sharpened_pick

    # Pass 3: rotation rescue. The BP UI tilts the right column ~+12°
    # and the left column ~-12°. We try a small set of angles on the
    # *original* crop only (skipping the sharpened copy) — empirically
    # the sharpening matters more for orientation 0 where edges are
    # already aligned with the rec model's training distribution, and
    # rotation matters more when the strip is genuinely slanted. Bail
    # the moment we get a high-confidence read.
    try:
        from PIL import Image
    except ImportError:
        return best if best is not None else ("", 0.0)
    if best is not None and best[1] >= 0.90:
        # Already a high-confidence player name — no need to spin up
        # extra OCR passes that cost ~1s each.
        return best
    for angle in (-12, 12, -8):
        try:
            rotated = crop.rotate(
                angle, expand=True, fillcolor=255,
                resample=Image.BILINEAR,
            )
        except Exception:
            continue
        candidates = _ocr_pil_blocks(rotated, languages=languages)
        rot_pick = _select_best_player_block(candidates)
        if rot_pick is None:
            continue
        if best is None or rot_pick[1] > best[1]:
            best = rot_pick
            if best[1] >= 0.90:
                return best
    return best if best is not None else ("", 0.0)


def _sharpen(crop):
    """Return an unsharp-masked copy of ``crop`` for the OCR rescue pass.

    Returns ``None`` if PIL's filter module isn't importable. The
    parameters lean toward strong stroke contrast (radius 1, percent
    180) — the goal is to make blurry slanted glyphs readable, not
    to keep the image looking pretty.
    """
    try:
        from PIL import ImageFilter
    except ImportError:
        return None
    try:
        return crop.filter(
            ImageFilter.UnsharpMask(radius=1.0, percent=180, threshold=2)
        )
    except Exception:
        return None


def _select_best_player_block(
    candidates: list[tuple[str, float]],
) -> tuple[str, float] | None:
    """Apply the player-name filter to a list of (text, conf) and pick one.

    Returns ``None`` when no block survives the filter.
    """
    keep: list[tuple[str, float]] = []
    for text, conf in candidates:
        cleaned = (text or "").strip().strip(" 　·.,\"'“”-—_=")
        if not cleaned or len(cleaned) < 2:
            continue
        # Reject blocks made entirely of punctuation / dashes — these
        # are the avatar border bits the OCR sometimes hallucinates
        # as text.
        if not any(ch.isalnum() for ch in cleaned):
            continue
        if is_hero_name(cleaned):
            continue
        if _is_probably_ui_chrome(cleaned):
            continue
        keep.append((cleaned, conf))
    if not keep:
        return None
    # Prefer longer texts — real player names tend to be 4+ chars in
    # both Latin and CJK, while the slop-text we see (e.g. ``"3"``,
    # ``"-"``) is short.
    keep.sort(key=lambda tc: (-len(tc[0]), -tc[1]))
    return keep[0]


def _ocr_pil_blocks(im, *, languages: list[str] | None) -> list[tuple[str, float]]:
    """Run OCR over an in-memory PIL image, return every block as (text, conf).

    Skips the PNG-encode + tempfile round-trip ``recognize`` pays when
    handed a path. The per-slot rescue pipeline calls this ~10 times
    per BP screenshot, so the savings compound — especially on Windows
    where %TEMP% writes are slowed by Defender real-time scanning.
    """
    try:
        blocks = recognize_array(im, languages=languages)
    except Exception:
        return []
    return [(b.text.strip(), float(b.confidence)) for b in blocks]


def _ocr_pil_image(im, *, languages: list[str] | None) -> tuple[str, float]:
    """Run OCR over an in-memory PIL image, return (best_text, confidence)."""
    try:
        blocks = recognize_array(im, languages=languages)
    except Exception:
        return "", 0.0
    if not blocks:
        return "", 0.0
    best = max(blocks, key=lambda b: b.confidence)
    return best.text.strip(), float(best.confidence)
