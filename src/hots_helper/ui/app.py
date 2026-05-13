"""Entry point for the PySide6 desktop UI."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ..config import Config, default_db_path
from ..db import Store
from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HotS Helper")
    app.setQuitOnLastWindowClosed(True)

    config = Config.load()
    store = Store(default_db_path())

    window = MainWindow(store, config)
    window.show()

    rc = app.exec()
    store.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
