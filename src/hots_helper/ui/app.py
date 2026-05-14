"""Entry point for the PySide6 desktop UI."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ..config import Config, default_db_path as user_default_db_path
from ..db import Store
from .main_window import MainWindow


def _setup_logging() -> None:
    """Verbose stderr logging so the user can copy/paste a traceback when
    something goes wrong with OCR or screen capture."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _resolve_db_path() -> Path:
    """Pick the database file the UI should use.

    Preference order:
    1. ``data/hots.db`` next to the project root, if it exists. This is the DB
       the CLI writes to and the one we ship in git, so a fresh ``git pull``
       on Windows immediately reuses the analysis from the Mac side.
    2. The user-data dir (``%APPDATA%\\hots-helper\\hots.db`` on Windows /
       ``~/Library/Application Support/hots-helper/`` on macOS) — the
       legacy location for an installed/PyInstaller build.

    If the user-data DB exists but the project DB doesn't, we use the user
    one so we don't lose data from a previously-running install.
    """
    here = Path(__file__).resolve().parents[3]
    project_db = here / "data" / "hots.db"
    if project_db.exists():
        return project_db
    user_db = user_default_db_path()
    return user_db if user_db.exists() else project_db


def main() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("HotS Helper")
    app.setQuitOnLastWindowClosed(True)

    config = Config.load()
    db_path = _resolve_db_path()
    store = Store(db_path)
    print(f"[hots-ui] using database: {db_path}")

    window = MainWindow(store, config)
    window._log(f"DB: {db_path}")
    window.show()

    rc = app.exec()
    store.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
