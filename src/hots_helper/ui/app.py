"""Entry point for the PySide6 desktop UI."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from ..config import Config, default_db_path
from ..db import Store
from ..i18n import set_language, t
from .assets import app_icon
from .main_window import MainWindow
from .theme import apply_app_theme


def _setup_logging() -> None:
    """Verbose stderr logging so the user can copy/paste a traceback when
    something goes wrong with OCR or screen capture."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("HotS Helper")
    app.setQuitOnLastWindowClosed(True)
    app.setWindowIcon(app_icon())
    apply_app_theme(app)

    config = Config.load()
    set_language(getattr(config, "language", "zh") or "zh")

    # DB lives in the user-data dir on every platform now; cloud sync
    # repopulates a fresh install on first launch.
    db_path = default_db_path()
    store = Store(db_path)
    print(f"[hots-ui] using database: {db_path}")

    window = MainWindow(store, config)
    window._log(t("ui.main.db_path", path=db_path))
    window.show()

    rc = app.exec()
    store.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
