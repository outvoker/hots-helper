"""RapidOCR backend (PaddleOCR ported to ONNX Runtime), multi-language.

Cross-platform, pure Python wheels, no system dependencies. Bundles three
recognition models so we cover Chinese / English / Korean / Japanese
glyphs from the same pipeline, regardless of what language packs the
end user has installed at the OS level:

* ``ch_PP-OCRv4_rec_infer.onnx``     — bundled with rapidocr-onnxruntime;
                                       Chinese characters + Latin alphabet.
* ``korean_mobile_v2.0_rec_infer.onnx`` — committed to ``ocr/models/``;
                                          Hangul + Latin (~3 MB).
* ``japan_rec_crnn.onnx``            — committed to ``ocr/models/``;
                                       Hiragana + Katakana + Kanji + Latin
                                       (~3 MB, fixed input height 32).

Each detected text box gets passed through every recognition model.
The variant that returns the highest confidence wins for that box —
Korean glyphs come out as garbage (low confidence) from the Chinese
model and as the right text (high confidence) from the Korean model.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import OcrBlock

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str], None]]

_MODELS_DIR = Path(__file__).resolve().parent / "models"

# Lazy-initialised singletons keyed by language tag. Building each engine
# costs ~0.5–1 s on a cold start; reuse keeps subsequent screenshots fast.
#
# Note: we used to run the three language passes in parallel via a
# ThreadPoolExecutor, but ONNX Runtime sessions each grab every CPU
# core by default (intra-op + OpenMP), and three sessions racing for
# the same cores caused massive oversubscription — the wall-clock
# went from ~2.5s sequential to ~40s parallel. Until we either share
# the det stage across passes or pin per-session thread counts, run
# them serially.
_engines: dict[str, "object | None"] = {}


@dataclass(frozen=True)
class _LangSpec:
    tag: str
    rec_model_path: Path | None  # None = use rapidocr's bundled default
    rec_img_shape: tuple[int, int, int] | None  # None = use rapidocr's default
    # Optional override for the detection model and the recogniser's
    # character dictionary. ``None`` falls back to the rapidocr-bundled
    # PP-OCRv4 det / rec-default keys.
    det_model_path: Path | None = None
    rec_keys_path: Path | None = None


# Order matters only for log messages — confidence merge is order-independent.
#
# We standardise on **PP-OCRv5 mobile** for cn+en and Korean. Compared to
# the v4 / v1 mobile models the rapidocr wheel ships, v5 is
# significantly stronger at small / slanted UI text — directly improving
# player-name recognition on the slanted KR-server BP screens we kept
# missing under v4. The shared v5 detector also boosts recall for both
# language passes.
#
# Models live in ``ocr/models/``:
#   * ``ppocrv5_mobile_det.onnx``         — det shared by all passes (4.6 MB)
#   * ``ppocrv5_mobile_rec.onnx`` + ``ppocrv5_keys_v1.txt`` — cn+en rec (16 MB)
#   * ``korean_ppocrv5_mobile_rec.onnx`` + ``korean_ppocrv5_keys_v1.txt``
#                                          — korean rec (13 MB)
#
# We also have ``ppocrv5_server_*`` variants (84 + 81 MB) bundled for
# completeness, but the wall-clock cost (~50 s per screenshot vs ~5 s
# for mobile) doesn't justify the marginal +1 slot accuracy gain on
# our test set — squad members run this on a hotkey during the live
# BP draft phase. Mobile stays the default; switching to server is a
# tweak in this file when GPU acceleration is available.
_LANGS: tuple[_LangSpec, ...] = (
    _LangSpec(
        tag="cn+en",
        det_model_path=_MODELS_DIR / "ppocrv5_mobile_det.onnx",
        rec_model_path=_MODELS_DIR / "ppocrv5_mobile_rec.onnx",
        rec_keys_path=_MODELS_DIR / "ppocrv5_keys_v1.txt",
        rec_img_shape=(3, 48, 320),
    ),
    _LangSpec(
        tag="korean",
        det_model_path=_MODELS_DIR / "ppocrv5_mobile_det.onnx",
        rec_model_path=_MODELS_DIR / "korean_ppocrv5_mobile_rec.onnx",
        rec_keys_path=_MODELS_DIR / "korean_ppocrv5_keys_v1.txt",
        rec_img_shape=(3, 48, 320),
    ),
    _LangSpec(
        tag="japanese",
        rec_model_path=_MODELS_DIR / "japan_rec_crnn.onnx",
        # The Japanese CRNN model has a fixed input height of 32 rather
        # than the 48 PP-OCRv5 expects — pass the right shape so
        # rapidocr's normaliser doesn't squash the glyphs.
        rec_img_shape=(3, 32, 320),
    ),
)


def _emit(progress: ProgressCallback, msg: str) -> None:
    logger.info(msg)
    if progress is not None:
        try:
            progress(msg)
        except Exception:
            pass


def _get_engine(spec: _LangSpec):
    """Return a cached RapidOCR engine for the given language spec."""
    cached = _engines.get(spec.tag)
    if cached is not None:
        return cached
    from rapidocr_onnxruntime import RapidOCR

    kwargs: dict = {}
    if spec.det_model_path is not None:
        if not spec.det_model_path.is_file():
            raise FileNotFoundError(
                f"missing OCR det model {spec.det_model_path}"
            )
        kwargs["det_model_path"] = str(spec.det_model_path)
    if spec.rec_model_path is not None:
        if not spec.rec_model_path.is_file():
            raise FileNotFoundError(
                f"missing OCR rec model {spec.rec_model_path}"
            )
        kwargs["rec_model_path"] = str(spec.rec_model_path)
    if spec.rec_keys_path is not None:
        if not spec.rec_keys_path.is_file():
            raise FileNotFoundError(
                f"missing OCR rec dict {spec.rec_keys_path}"
            )
        kwargs["rec_keys_path"] = str(spec.rec_keys_path)
    if spec.rec_img_shape is not None:
        kwargs["rec_img_shape"] = list(spec.rec_img_shape)
    engine = RapidOCR(**kwargs)
    _engines[spec.tag] = engine
    return engine


# --- bbox helpers ---------------------------------------------------------


def _box_iou(a: tuple[float, float, float, float],
             b: tuple[float, float, float, float]) -> float:
    """Intersection-over-union for normalised (x0, y0, x1, y1) boxes."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, (ax1 - ax0)) * max(0.0, (ay1 - ay0))
    area_b = max(0.0, (bx1 - bx0)) * max(0.0, (by1 - by0))
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def _merge_blocks(passes: list[list[OcrBlock]],
                  iou_threshold: float = 0.4,
                  unique_confidence_floor: float = 0.7) -> list[OcrBlock]:
    """Combine recognition results from multiple language passes.

    Each detected text region appears once per language pass at roughly
    the same bbox; pick the language whose recognizer was most
    confident. Blocks that *only* appear in one pass and never overlap
    with anything from another pass are kept only if their confidence
    is above ``unique_confidence_floor`` — these are usually
    hallucinations the JP/KR mobile models produce on textured
    background where the CN+EN model correctly detected nothing.
    """
    # First merge: track every overlapping family.
    merged: list[OcrBlock] = []
    overlap_count: list[int] = []  # parallel to merged
    for pass_blocks in passes:
        for cand in pass_blocks:
            best_existing_idx = -1
            best_iou = 0.0
            for i, kept in enumerate(merged):
                iou = _box_iou(cand.bbox, kept.bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_existing_idx = i
            if best_iou >= iou_threshold and best_existing_idx >= 0:
                overlap_count[best_existing_idx] += 1
                if cand.confidence > merged[best_existing_idx].confidence:
                    merged[best_existing_idx] = cand
            else:
                merged.append(cand)
                overlap_count.append(1)

    # Second pass: drop solitary low-confidence blocks. A real text
    # region almost always gets detected by the shared det model and
    # rec'd by at least the CN+EN pass, so genuine text has
    # overlap_count >= 2 (often 3). Solitary blocks under the floor
    # are the JP/KR mobile models seeing patterns in noise.
    #
    # The "solitary == hallucination" heuristic only makes sense when
    # we ran more than one pass. With a single pass every block is
    # solitary by definition; filtering would silently drop real
    # text. So skip the filter entirely in that case.
    if len(passes) <= 1:
        return list(merged)
    final: list[OcrBlock] = []
    for block, n in zip(merged, overlap_count):
        if n == 1 and block.confidence < unique_confidence_floor:
            continue
        final.append(block)
    return final


# --- one-language pass ----------------------------------------------------


def _run_pass(
    engine,
    image_path: Path,
    img_w: float,
    img_h: float,
) -> list[OcrBlock]:
    try:
        result, _elapse = engine(str(image_path))
    except Exception as e:
        logger.exception("RapidOCR call failed: %s", e)
        return []
    if not result:
        return []
    blocks: list[OcrBlock] = []
    for entry in result:
        box, text, conf = entry
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


# --- public API -----------------------------------------------------------


def recognize(image_path: Path,
              progress: ProgressCallback = None,
              languages: list[str] | None = None) -> list[OcrBlock]:
    """Run RapidOCR over ``image_path``.

    ``languages`` selects which engines run; values must match the
    ``tag`` field of one of :data:`_LANGS`. ``None`` means "use every
    bundled engine" — useful for one-off scripts. The UI passes its
    user-configured subset (cheaper to run fewer passes; each one is
    ~1s of wall time).
    """
    t0 = time.monotonic()
    _emit(progress, "loading RapidOCR engines…")

    # Resolve which engine specs to actually run this call.
    if languages is None:
        active_langs = _LANGS
    else:
        wanted = set(languages)
        active_langs = tuple(s for s in _LANGS if s.tag in wanted)
        if not active_langs:
            # Empty / all-unknown selection — fall back to CN+EN so we
            # don't silently return zero blocks. CN+EN is the cheapest
            # pass and covers the squad's most common case.
            active_langs = tuple(s for s in _LANGS if s.tag == "cn+en")

    # Resolve image dimensions once so every pass produces normalised bboxes.
    img_w: float
    img_h: float
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            img_w, img_h = float(im.size[0]), float(im.size[1])
    except Exception as e:
        _emit(progress, f"image stat failed: {e}")
        return []

    # Run each language's recognition pass serially. Parallelising
    # them via a thread pool sounded great on paper (each engine has
    # its own ONNX session, sessions are thread-safe) but in practice
    # ONNX Runtime sessions saturate every core by default; three
    # sessions racing for the same cores caused massive thread
    # oversubscription and pushed wall-clock from ~2.5s sequential
    # to ~40s "parallel". Until we share the det stage or pin
    # per-session thread counts, serial is faster.
    passes: list[list[OcrBlock]] = []
    for spec in active_langs:
        try:
            engine = _get_engine(spec)
        except FileNotFoundError as e:
            _emit(progress, f"  [{spec.tag}] model missing — skipping ({e})")
            continue
        except Exception as e:
            _emit(
                progress,
                f"  [{spec.tag}] init failed: {type(e).__name__}: {e}",
            )
            continue
        try:
            t_pass = time.monotonic()
            blocks = _run_pass(engine, image_path, img_w, img_h)
            logger.info(
                "  [%s] %d block(s) in %.2fs",
                spec.tag, len(blocks), time.monotonic() - t_pass,
            )
            passes.append(blocks)
            _emit(progress, f"  [{spec.tag}] {len(blocks)} block(s)")
        except Exception as e:
            _emit(
                progress,
                f"  [{spec.tag}] failed: {type(e).__name__}: {e}",
            )

    if not passes:
        _emit(progress, "no OCR engines available — returning empty result")
        return []

    merged = _merge_blocks(passes)
    _emit(
        progress,
        f"OCR done — {len(merged)} block(s) merged from {len(passes)} pass(es) "
        f"in {time.monotonic() - t0:.2f}s",
    )
    return merged
