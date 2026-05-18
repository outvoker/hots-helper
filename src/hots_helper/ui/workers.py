"""Background workers the UI talks to via Qt signals."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..db import Store
from ..watcher.ingest import IngestResult, ingest_directory, ingest_file

_EXT = ".StormReplay"


class ScanWorker(QObject):
    """Ingest every replay under the given directories in a background thread."""

    progress = Signal(IngestResult)
    finished = Signal(int, int, int)  # new, skipped, errors

    def __init__(self, store: Store, directories: list[Path]) -> None:
        super().__init__()
        self.store = store
        self.directories = directories

    def run(self) -> None:
        new = skipped = errors = 0
        for d in self.directories:
            for r in ingest_directory(self.store, d):
                self.progress.emit(r)
                if r.error:
                    errors += 1
                elif r.inserted:
                    new += 1
                else:
                    skipped += 1
        self.finished.emit(new, skipped, errors)


def _wait_for_stable(path: Path, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    last_size = -1
    hits = 0
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last_size and size > 0:
            hits += 1
            if hits >= 3:
                return True
        else:
            hits = 0
        last_size = size
        time.sleep(0.5)
    return False


class _WatchHandler(FileSystemEventHandler):
    def __init__(self, worker: "WatchWorker") -> None:
        super().__init__()
        self.worker = worker

    def _handle(self, path_str: str) -> None:
        path = Path(path_str)
        if path.suffix != _EXT or not path.is_file():
            return
        if not _wait_for_stable(path):
            return
        result = ingest_file(self.worker.store, path)
        self.worker.ingested.emit(result)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(event.dest_path)


class WatchWorker(QObject):
    """Persistent watcher the UI toggles on/off."""

    ingested = Signal(IngestResult)
    started_watching = Signal(list)  # list[str] of directories
    stopped = Signal()

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self._observer: Observer | None = None

    def start(self, directories: list[Path]) -> None:
        self.stop()
        if not directories:
            return
        observer = Observer()
        handler = _WatchHandler(self)
        for d in directories:
            observer.schedule(handler, str(d), recursive=False)
        observer.start()
        self._observer = observer
        self.started_watching.emit([str(d) for d in directories])

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
            self.stopped.emit()


class SyncWorker(QObject):
    """Run a single CloudSync.sync_now() round in a background thread."""

    progress = Signal(str)
    finished = Signal(object)  # SyncResult

    def __init__(self, sync) -> None:
        super().__init__()
        self.sync = sync

    def run(self) -> None:
        from ..sync import SyncResult
        try:
            result = self.sync.sync_now(progress=self.progress.emit)
        except Exception as e:
            traceback.print_exc()
            result = SyncResult(0, 0, 0, 0, 0, 0, [f"{type(e).__name__}: {e}"])
        self.finished.emit(result)


@dataclass
class HotkeyShotResult:
    """Outcome of one hotkey-triggered capture+OCR run."""
    screenshot_path: Path | None
    map_name: str
    ally_names: list[str]
    enemy_names: list[str]
    ally_confidences: list[float]
    enemy_confidences: list[float]
    drafter: str
    log_lines: list[str]


class HotkeyWorker(QObject):
    """Runs the screenshot + OCR pipeline off the Qt main thread.

    Both steps can take >1s on Windows: ``mss`` writes a multi-megabyte PNG
    and Windows.Media.Ocr blocks on multiple language passes. Doing them on
    the main thread freezes the UI; on Windows the situation is worse because
    ``winrt`` calls also need a COM apartment, which they don't get when
    invoked from arbitrary threads. We give this worker its own QThread so
    Qt manages the COM init / event loop for us.

    Emits ``progress`` for each stage so the UI can show what's happening
    even when the pipeline takes several seconds. ``finished`` carries the
    final result.
    """

    progress = Signal(str)
    # Emitted as soon as the screenshot is on disk and the helper UI
    # is safe to redisplay. Lets the main window show the capture
    # progress dialog and restore the floating launcher *after* the
    # frame is grabbed instead of letting them slip into the shot.
    screenshot_taken = Signal()
    finished = Signal(object)  # HotkeyShotResult

    def __init__(
        self,
        sample_path: Path | None = None,
        ocr_languages: list[str] | None = None,
    ) -> None:
        super().__init__()
        # If set, skip the live screenshot stage and use this image
        # instead. Used by the BP card's "样例测试 / Sample" button so
        # the user can see the popup without being in a real game.
        self._sample_path: Path | None = sample_path
        # Per-call OCR engine selection passed from the UI's config.
        # ``None`` = use whatever rapid.recognize() defaults to.
        self._ocr_languages: list[str] | None = ocr_languages

    def run(self) -> None:
        log_lines: list[str] = []
        screenshot_path: Path | None = None

        # Stage 1: screenshot — or load the bundled sample.
        t0 = time.monotonic()
        if self._sample_path is not None:
            self.progress.emit("[1/3] Loading sample BP screenshot…")
            try:
                p = Path(self._sample_path)
                if not p.is_file():
                    raise FileNotFoundError(p)
                screenshot_path = p
                log_lines.append(f"[1/3] Using sample image: {p}")
                self.progress.emit("[1/3] Sample loaded — running OCR next")
            except Exception as e:
                log_lines.append(
                    f"[1/3 sample load error] {type(e).__name__}: {e}"
                )
                log_lines.append(traceback.format_exc())
            # No real frame was grabbed, but we still want the helper
            # UI restored so the rest of the pipeline can update it.
            self.screenshot_taken.emit()
        else:
            self.progress.emit("[1/3] Capturing screenshot…")
            try:
                from .screenshot import capture_fullscreen
                # Give the desktop compositor a beat to repaint without
                # the helper's own overlays (the main window hid them
                # right before kicking off this worker). 60 ms is a
                # conservative ~3 frames at 60Hz / 4 frames at 75Hz —
                # enough to cover slow Windows DWM updates without a
                # noticeable user-perceived lag.
                QThread.msleep(60)
                screenshot_path = capture_fullscreen()
                # Frame is on disk — UI can come back now without
                # contaminating subsequent passes.
                self.screenshot_taken.emit()
                dt = time.monotonic() - t0
                log_lines.append(f"[1/3] Screenshot saved in {dt:.1f}s: {screenshot_path}")
                self.progress.emit(f"[1/3] Screenshot done ({dt:.1f}s)")
            except Exception as e:
                log_lines.append(f"[1/3 screenshot error] {type(e).__name__}: {e}")
                log_lines.append(traceback.format_exc())
                # Restore the helper UI even on capture failure so the
                # user sees the error popup instead of an invisible app.
                self.screenshot_taken.emit()

        map_name = ""
        allies: list[str] = [""] * 5
        enemies: list[str] = [""] * 5
        ally_conf: list[float] = [0.0] * 5
        enemy_conf: list[float] = [0.0] * 5
        drafter = ""

        if screenshot_path is not None:
            # Stage 2: low-level OCR (system engine pass)
            t1 = time.monotonic()
            self.progress.emit(
                "[2/3] Running system OCR (this may take 1-3s on Windows)…"
            )
            blocks = []
            try:
                from ..ocr import recognize

                # Stream low-level OCR stage messages to the UI as they
                # happen. Without this the user just sees "Running OCR..."
                # and waits in the dark when winrt is slow.
                def _ocr_progress(msg: str) -> None:
                    self.progress.emit(f"      {msg}")
                    log_lines.append(f"      {msg}")

                blocks = recognize(
                    screenshot_path,
                    progress=_ocr_progress,
                    languages=self._ocr_languages,
                )
                dt = time.monotonic() - t1
                log_lines.append(
                    f"[2/3] OCR returned {len(blocks)} text block(s) in {dt:.1f}s"
                )
                self.progress.emit(
                    f"[2/3] OCR done — {len(blocks)} text blocks ({dt:.1f}s)"
                )
                # Dump every recognized block so the user can paste the log
                # to debug missed slots. Sorted top-to-bottom for readability.
                for b in sorted(blocks, key=lambda b: b.bbox[1]):
                    x0, y0, x1, y1 = b.bbox
                    cx = (x0 + x1) / 2
                    cy = (y0 + y1) / 2
                    log_lines.append(
                        f"      block cx={cx:.3f} cy={cy:.3f} "
                        f"conf={b.confidence:.2f} {b.text!r}"
                    )
            except Exception as e:
                log_lines.append(f"[2/3 OCR error] {type(e).__name__}: {e}")
                log_lines.append(traceback.format_exc())
                self.progress.emit(f"[2/3] OCR FAILED: {type(e).__name__}: {e}")

            # Stage 3: bucket the blocks into map + 5 allies + 5 enemies
            if blocks:
                t2 = time.monotonic()
                self.progress.emit("[3/3] Parsing names from OCR blocks…")
                try:
                    from ..vision import parse_screenshot
                    parsed = parse_screenshot(screenshot_path, blocks=blocks)
                    map_name = parsed.map_name
                    allies = list(parsed.ally_names)
                    enemies = list(parsed.enemy_names)
                    ally_conf = list(parsed.ally_confidences)
                    enemy_conf = list(parsed.enemy_confidences)
                    drafter = parsed.drafter
                    dt = time.monotonic() - t2
                    if parsed.anything_found:
                        log_lines.append(
                            f"[3/3] Parsed in {dt:.1f}s: map={parsed.map_name!r}"
                        )
                        log_lines.append(f"      allies={parsed.ally_names}")
                        log_lines.append(f"      enemies={parsed.enemy_names}")
                        if drafter:
                            log_lines.append(f"      drafter={drafter}")
                        self.progress.emit(
                            f"[3/3] Done. map={parsed.map_name!r}, "
                            f"{sum(1 for n in allies if n)}/5 allies, "
                            f"{sum(1 for n in enemies if n)}/5 enemies"
                        )
                    else:
                        log_lines.append(
                            "[3/3] OCR ran but no text matched the BP layout — "
                            "is the screen really on the draft phase?"
                        )
                        self.progress.emit("[3/3] No BP layout detected")
                except Exception as e:
                    log_lines.append(
                        f"[3/3 parse error] {type(e).__name__}: {e}"
                    )
                    log_lines.append(traceback.format_exc())
                    self.progress.emit(
                        f"[3/3] Parse FAILED: {type(e).__name__}: {e}"
                    )

        total = time.monotonic() - t0
        log_lines.append(f"Total pipeline time: {total:.1f}s")

        result = HotkeyShotResult(
            screenshot_path=screenshot_path,
            map_name=map_name,
            ally_names=allies,
            enemy_names=enemies,
            ally_confidences=ally_conf,
            enemy_confidences=enemy_conf,
            drafter=drafter,
            log_lines=log_lines,
        )
        self.finished.emit(result)


# --- chat OCR + translate -------------------------------------------------


@dataclass
class ChatTranslationResult:
    """Outcome of a single chat-OCR + translate run."""
    screenshot_path: Path | None
    # Pairs of (original chat line, translated to zh).
    pairs: list[tuple[str, str]] = field(default_factory=list)
    # Detected source-language code per row (e.g. "ko", "ja"). Same length
    # as ``pairs``; empty string if VolcEngine didn't return one.
    detected_sources: list[str] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    error: str = ""


class ChatTranslateWorker(QObject):
    """Capture the screen for the chat-translate flow and stop there.

    The OCR + translate stages used to live here too — the worker would
    OCR the whole screen and try to filter to a "chat box" rectangle.
    Auto-detection wasn't reliable across resolutions / windowed-vs-
    fullscreen, and full-screen OCR was slow even when the chat box
    only occupied 5% of the frame. The flow is now:

        capture (this worker)  →  user drags a rectangle
                               →  ChatCropTranslateWorker (small crop OCR + translate)
                               →  popup shows the lines

    so this worker just hands the screenshot path to the main window
    and exits. ``ocr_languages`` / ``target_lang`` are kept on the
    constructor for backwards compatibility with existing call sites,
    but neither is used here — they're forwarded by the main window
    to the crop worker once the user has framed the chat region.
    """

    progress = Signal(str)
    # Same role as HotkeyWorker.screenshot_taken — emitted once the
    # frame is on disk so the main window can re-show its UI without
    # the launcher chip / progress dialog leaking into the captured
    # image.
    screenshot_taken = Signal()
    finished = Signal(object)  # ChatTranslationResult

    def __init__(
        self,
        target_lang: str = "zh",
        ocr_languages: list[str] | None = None,
    ) -> None:
        super().__init__()
        # Kept on the instance so the main window can reach them after
        # the screenshot lands and pass them on to the crop worker.
        self._target_lang = target_lang
        self._ocr_languages: list[str] | None = ocr_languages

    def run(self) -> None:
        from .screenshot import capture_fullscreen

        log_lines: list[str] = []
        self.progress.emit("[1/2] Capturing screen…")
        try:
            QThread.msleep(60)
            screenshot_path = capture_fullscreen()
            self.screenshot_taken.emit()
            log_lines.append(f"[1/2] Screenshot: {screenshot_path}")
        except Exception as e:
            log_lines.append(
                f"[1/2 screenshot error] {type(e).__name__}: {e}"
            )
            log_lines.append(traceback.format_exc())
            self.screenshot_taken.emit()
            self.finished.emit(ChatTranslationResult(
                screenshot_path=None,
                error=f"截图失败：{e}",
                log_lines=log_lines,
            ))
            return
        self.finished.emit(ChatTranslationResult(
            screenshot_path=screenshot_path,
            log_lines=log_lines,
        ))


class ChatCropTranslateWorker(QObject):
    """OCR a user-framed rectangle of an existing screenshot, translate
    every line to ``target_lang``, and emit a ChatTranslationResult.

    Used in two places:
    * the brand-new chat translate flow (capture → user picks region
      → this worker)
    * the chat translation popup's "重选区域" button, which lets the
      user re-frame after seeing the first result

    The crop is padded with 8px black so the OCR detector sees the full
    glyph extents even when the user drew a tight rectangle.
    """

    progress = Signal(str)
    finished = Signal(object)  # ChatTranslationResult

    def __init__(
        self,
        screenshot_path: Path,
        x: int, y: int, w: int, h: int,
        *,
        target_lang: str = "zh",
        ocr_languages: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._path = screenshot_path
        self._x = x
        self._y = y
        self._w = w
        self._h = h
        self._target_lang = target_lang
        self._ocr_languages: list[str] | None = ocr_languages

    def run(self) -> None:
        import tempfile
        from PIL import Image

        from ..chat_ocr import filter_chat_blocks
        from ..ocr import recognize
        from ..translate import TranslateError, translate

        log_lines: list[str] = []

        # 1. Crop + pad.
        self.progress.emit("[1/3] 裁剪选中区域…")
        try:
            with Image.open(self._path) as im:
                crop = im.crop(
                    (self._x, self._y, self._x + self._w, self._y + self._h)
                )
                pad = 8
                padded = Image.new(
                    "RGB",
                    (crop.width + 2 * pad, crop.height + 2 * pad),
                    (0, 0, 0),
                )
                padded.paste(crop, (pad, pad))
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    tmp_path = Path(f.name)
                padded.save(tmp_path)
        except Exception as e:
            log_lines.append(f"[1/3 crop error] {type(e).__name__}: {e}")
            log_lines.append(traceback.format_exc())
            self.finished.emit(ChatTranslationResult(
                screenshot_path=self._path,
                error=f"裁剪失败：{e}",
                log_lines=log_lines,
            ))
            return

        # 2. OCR the crop.
        self.progress.emit("[2/3] 识别选中区域文字…")
        t1 = time.monotonic()
        try:
            blocks = recognize(
                tmp_path,
                progress=lambda m: self.progress.emit(f"      {m}"),
                languages=self._ocr_languages,
            )
            log_lines.append(
                f"[2/3] OCR {len(blocks)} block(s) in {time.monotonic()-t1:.1f}s"
            )
        except Exception as e:
            log_lines.append(f"[2/3 OCR error] {type(e).__name__}: {e}")
            log_lines.append(traceback.format_exc())
            self.finished.emit(ChatTranslationResult(
                screenshot_path=self._path,
                error=f"OCR 失败：{e}",
                log_lines=log_lines,
            ))
            return
        finally:
            tmp_path.unlink(missing_ok=True)

        chat = filter_chat_blocks(blocks)
        log_lines.append(
            f"[2/3] {len(chat)} block(s) survive content filter"
        )
        if not chat:
            self.finished.emit(ChatTranslationResult(
                screenshot_path=self._path,
                pairs=[],
                log_lines=log_lines,
            ))
            return

        # 3. Translate.
        self.progress.emit(f"[3/3] 翻译 {len(chat)} 行…")
        try:
            results = translate(
                [c.text for c in chat],
                target=self._target_lang,
                source="auto",
            )
        except TranslateError as e:
            log_lines.append(f"[3/3 translate error] {e}")
            self.finished.emit(ChatTranslationResult(
                screenshot_path=self._path,
                error=f"翻译失败：{e}",
                log_lines=log_lines,
            ))
            return

        pairs = [(c.text, r.text) for c, r in zip(chat, results)]
        sources = [r.detected_source for r in results]
        log_lines.append(f"[3/3] Translated {len(pairs)} line(s)")
        self.finished.emit(ChatTranslationResult(
            screenshot_path=self._path,
            pairs=pairs,
            detected_sources=sources,
            log_lines=log_lines,
        ))


# --- compose translate (zh → target) --------------------------------------


@dataclass
class ComposeTranslationResult:
    text: str = ""
    error: str = ""


class ComposeTranslateWorker(QObject):
    """Translate one Chinese phrase to one target language. Tiny worker
    — we still off-thread it so the UI never blocks on the network call."""

    progress = Signal(str)
    finished = Signal(object)  # ComposeTranslationResult

    def __init__(self, text: str, target: str) -> None:
        super().__init__()
        self._text = text
        self._target = target

    def run(self) -> None:
        from ..translate import TranslateError, translate
        try:
            results = translate(
                [self._text],
                target=self._target,
                source="zh",
            )
        except TranslateError as e:
            self.finished.emit(ComposeTranslationResult(error=str(e)))
            return
        self.finished.emit(
            ComposeTranslationResult(text=results[0].text if results else "")
        )
