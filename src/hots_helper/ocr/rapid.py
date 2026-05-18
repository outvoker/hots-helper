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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import OcrBlock

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str], None]]

_MODELS_DIR = Path(__file__).resolve().parent / "models"

# Lazy-initialised singletons keyed by language tag. Building each engine
# costs ~0.5–1 s on a cold start; reuse keeps subsequent screenshots fast.
_engines: dict[str, "object | None"] = {}
_engine_lock = threading.Lock()

# Workers reused across recognize() calls. We keep three threads — one
# per language pass — so the typical case is "submit three jobs, wait
# for all" without re-creating threads per screenshot.
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=3,
                thread_name_prefix="rapidocr",
            )
        return _pool


@dataclass(frozen=True)
class _LangSpec:
    tag: str
    rec_model_path: Path | None  # None = use rapidocr's bundled default
    rec_img_shape: tuple[int, int, int] | None  # None = use rapidocr's default


# Order matters only for log messages — confidence merge is order-independent.
_LANGS: tuple[_LangSpec, ...] = (
    # Bundled CN+EN model. Path stays None so rapidocr resolves its own
    # default at engine-init time (the .onnx ships inside the wheel).
    _LangSpec(tag="cn+en", rec_model_path=None, rec_img_shape=None),
    _LangSpec(
        tag="korean",
        rec_model_path=_MODELS_DIR / "korean_mobile_v2.0_rec_infer.onnx",
        # PP-OCRv1 Korean model accepts the default 48-tall input fine.
        rec_img_shape=None,
    ),
    _LangSpec(
        tag="japanese",
        rec_model_path=_MODELS_DIR / "japan_rec_crnn.onnx",
        # The Japanese CRNN model has a fixed input height of 32 rather
        # than the 48 PP-OCRv4 expects — pass the right shape so
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
    """Return a cached RapidOCR engine for the given language spec.

    Thread-safe: the three parallel passes hit this concurrently on
    first call. Without the lock, two threads would race to build the
    same engine and double the (ONNX-heavy) init cost.
    """
    cached = _engines.get(spec.tag)
    if cached is not None:
        return cached
    with _engine_lock:
        cached = _engines.get(spec.tag)
        if cached is not None:
            return cached
        from rapidocr_onnxruntime import RapidOCR

        kwargs: dict = {}
        if spec.rec_model_path is not None:
            if not spec.rec_model_path.is_file():
                raise FileNotFoundError(
                    f"missing OCR model {spec.rec_model_path}"
                )
            kwargs["rec_model_path"] = str(spec.rec_model_path)
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
              progress: ProgressCallback = None) -> list[OcrBlock]:
    t0 = time.monotonic()
    _emit(progress, "loading RapidOCR engines…")

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

    # Submit each language's recognition pass to a small thread pool
    # and await the lot in parallel. ONNX Runtime sessions are
    # independently thread-safe (each engine owns its own session +
    # buffers) so the three passes don't serialise on shared state.
    # Wall-clock time goes from sum(pass) ≈ 2.5s to max(pass) ≈ 1s.
    def _job(spec: _LangSpec) -> tuple[_LangSpec, list[OcrBlock] | Exception]:
        try:
            engine = _get_engine(spec)
        except Exception as exc:
            return spec, exc
        try:
            t_pass = time.monotonic()
            blocks = _run_pass(engine, image_path, img_w, img_h)
            logger.info(
                "  [%s] %d block(s) in %.2fs",
                spec.tag, len(blocks), time.monotonic() - t_pass,
            )
            return spec, blocks
        except Exception as exc:
            return spec, exc

    pool = _get_pool()
    futures = [pool.submit(_job, s) for s in _LANGS]
    _emit(progress, f"  running {len(_LANGS)} language passes in parallel…")

    passes: list[list[OcrBlock]] = []
    for fut in futures:
        spec, outcome = fut.result()
        if isinstance(outcome, FileNotFoundError):
            _emit(progress, f"  [{spec.tag}] model missing — skipping ({outcome})")
            continue
        if isinstance(outcome, Exception):
            _emit(
                progress,
                f"  [{spec.tag}] failed: {type(outcome).__name__}: {outcome}",
            )
            continue
        passes.append(outcome)
        _emit(progress, f"  [{spec.tag}] {len(outcome)} block(s)")

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
