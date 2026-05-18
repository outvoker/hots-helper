"""macOS-specific tweaks so our floating widgets show up over a
fullscreen game.

When HotS runs fullscreen, macOS puts it in its own Mission Control
space; Qt's ``Qt.WindowStaysOnTopHint`` only floats the window inside
the *current* space, so the launcher / popups vanish from the user's
view as soon as they switch into the game space.

Setting the underlying NSWindow's ``collectionBehavior`` to
``canJoinAllSpaces | fullScreenAuxiliary | stationary`` makes the
window follow the user across spaces and float on top of fullscreen
apps. Setting the level to ``NSStatusWindowLevel`` keeps it above the
game's own top-level overlays.

This is a no-op on Windows / Linux, and a quiet no-op on macOS if
PyObjC isn't importable (e.g. dev environments without it).
"""

from __future__ import annotations

import sys
from typing import Iterable

from PySide6.QtWidgets import QWidget


def make_overlay_floating(widget: QWidget) -> None:
    """Apply the macOS overlay tweaks to ``widget``. Safe on every OS.

    Should be called *after* the first ``show()``: macOS only creates
    the backing NSWindow when the widget becomes visible, so calling
    this in ``__init__`` does nothing.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSStatusWindowLevel,
        )
    except Exception:
        return

    nsview_id = int(widget.winId())
    try:
        # Reach for the NSView via objc, then its window.
        import objc  # noqa: F401  (registers the bridge)
        from objc import objc_object

        nsview = objc_object(c_void_p=nsview_id)  # type: ignore[arg-type]
        nswindow = nsview.window()
        if nswindow is None:
            return
        nswindow.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        # Status window level sits above normal floating windows and
        # above fullscreen app overlays without going so high we cover
        # system menus (which would be a NSScreenSaverWindowLevel).
        nswindow.setLevel_(NSStatusWindowLevel)
    except Exception:
        # Best-effort: if any AppKit interop fails we just leave the
        # window with stock Qt behavior. Don't blow up the UI over a
        # quality-of-life tweak.
        return


def apply_to_all(widgets: Iterable[QWidget]) -> None:
    for w in widgets:
        if w is not None:
            make_overlay_floating(w)
