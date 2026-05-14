"""Background workers the UI talks to via Qt signals."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
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
    """

    finished = Signal(object)  # HotkeyShotResult

    def run(self) -> None:
        log_lines: list[str] = []
        screenshot_path: Path | None = None

        try:
            from .screenshot import capture_fullscreen
            screenshot_path = capture_fullscreen()
            log_lines.append(f"Screenshot saved: {screenshot_path}")
        except Exception as e:
            log_lines.append(f"[screenshot error] {e}")
            traceback.print_exc()

        map_name = ""
        allies: list[str] = [""] * 5
        enemies: list[str] = [""] * 5
        ally_conf: list[float] = [0.0] * 5
        enemy_conf: list[float] = [0.0] * 5
        drafter = ""

        if screenshot_path is not None:
            try:
                from ..vision import parse_screenshot

                parsed = parse_screenshot(screenshot_path)
                map_name = parsed.map_name
                allies = list(parsed.ally_names)
                enemies = list(parsed.enemy_names)
                ally_conf = list(parsed.ally_confidences)
                enemy_conf = list(parsed.enemy_confidences)
                drafter = parsed.drafter
                if parsed.anything_found:
                    log_lines.append(
                        f"OCR: map={parsed.map_name!r} "
                        f"allies={parsed.ally_names} enemies={parsed.enemy_names}"
                    )
                else:
                    log_lines.append("OCR: no text detected on this screenshot.")
            except Exception as e:
                log_lines.append(f"[OCR error] {type(e).__name__}: {e}")
                traceback.print_exc()

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
