"""Background workers the UI talks to via Qt signals."""

from __future__ import annotations

import time
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
