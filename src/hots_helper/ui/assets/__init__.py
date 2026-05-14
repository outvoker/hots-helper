"""Static UI assets shipped with the app — icon bundles, SVG, etc.

The PNG/ICO/ICNS files in this directory are generated from
``icon.svg`` by ``scripts/build_icons.py`` and committed so end-user
machines without Qt SVG runtime support still see the right icon.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap

_ASSETS_DIR = Path(__file__).resolve().parent

# Sizes we ship as standalone PNGs — keep in sync with scripts/build_icons.py.
_PNG_SIZES = (16, 32, 48, 64, 128, 256, 512)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Return a multi-resolution ``QIcon`` for the app.

    Qt picks the closest size automatically (taskbar, alt-tab, window
    title, dock). We pre-load every PNG we ship so HiDPI displays don't
    fall back to nearest-neighbour scaling from a single small PNG.
    """
    icon = QIcon()
    for size in _PNG_SIZES:
        path = _ASSETS_DIR / f"icon-{size}.png"
        if path.is_file():
            icon.addPixmap(QPixmap(str(path)))
    # Note: deliberately no SVG fallback here — that would require
    # bundling Qt's svg image-format plugin (~3 MB) just for an unlikely
    # case. The PNGs are committed and shipped alongside the .py file.
    return icon


def asset_path(name: str) -> Path:
    """Resolve a packaged asset name (``icon.ico``, ``icon-256.png``, …)."""
    return _ASSETS_DIR / name
