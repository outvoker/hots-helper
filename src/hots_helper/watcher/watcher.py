"""Watchdog-based recording folder watcher.

On startup we bootstrap-scan the whole directory, then listen for new or
modified ``.StormReplay`` files and ingest them. Blizzard writes the replay
atomically, but we still wait for the file size to stabilize before parsing to
avoid racing the writer.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from rich.console import Console
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..db import Store
from .ingest import IngestResult, ingest_directory, ingest_file

_EXT = ".StormReplay"
_STABLE_CHECKS = 3
_STABLE_INTERVAL = 0.5  # seconds


def _wait_for_stable(path: Path, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    last_size = -1
    stable_hits = 0
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last_size and size > 0:
            stable_hits += 1
            if stable_hits >= _STABLE_CHECKS:
                return True
        else:
            stable_hits = 0
        last_size = size
        time.sleep(_STABLE_INTERVAL)
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, store: Store, on_result: Callable[[IngestResult], None]) -> None:
        self.store = store
        self.on_result = on_result

    def _maybe_ingest(self, path_str: str) -> None:
        path = Path(path_str)
        if path.suffix != _EXT or not path.is_file():
            return
        if not _wait_for_stable(path):
            self.on_result(IngestResult(path=path, inserted=False, replay_id=None, error="file never stabilized"))
            return
        result = ingest_file(self.store, path)
        self.on_result(result)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_ingest(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_ingest(event.dest_path)


def watch(
    store: Store,
    directory: Path,
    *,
    bootstrap: bool = True,
    console: Console | None = None,
) -> None:
    console = console or Console()

    def _report(r: IngestResult) -> None:
        if r.error:
            console.print(f"[red]x[/red] {r.path.name}: {r.error}")
        elif r.inserted:
            console.print(f"[green]+[/green] ingested [bold]{r.path.name}[/bold] (replay_id={r.replay_id})")
        else:
            console.print(f"[dim]=[/dim] already known: {r.path.name}")

    if bootstrap:
        console.print(f"[cyan]Bootstrap scan of {directory}…[/cyan]")
        for result in ingest_directory(store, directory):
            _report(result)
        console.print(
            f"[cyan]Bootstrap done. replays={store.count_replays()} players={store.count_players()}[/cyan]"
        )

    handler = _Handler(store, _report)
    observer = Observer()
    observer.schedule(handler, str(directory), recursive=False)
    observer.start()
    console.print(f"[cyan]Watching {directory} for new replays. Ctrl+C to stop.[/cyan]")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("[yellow]Stopping watcher…[/yellow]")
    finally:
        observer.stop()
        observer.join()
