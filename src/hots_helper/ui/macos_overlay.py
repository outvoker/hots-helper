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


def make_overlay_floating(
    widget: QWidget, *, above_overlay: bool = False
) -> None:
    """Apply the macOS overlay tweaks to ``widget``. Safe on every OS.

    Should be called *after* the first ``show()``: macOS only creates
    the backing NSWindow when the widget becomes visible, so calling
    this in ``__init__`` does nothing.

    ``above_overlay``: when ``True`` the window is pinned at
    ``NSScreenSaverWindowLevel`` (1000) so it sits *above* the regular
    overlay layer used by the BP popup / launcher (which live at
    ``NSPopUpMenuWindowLevel`` = 101). Use this for child dialogs
    spawned from those overlays — otherwise the parent's
    ``orderFrontRegardless`` re-front cycles can cover the child.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSPopUpMenuWindowLevel,
            NSScreenSaverWindowLevel,
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
        # NSStatusWindowLevel (25) wasn't high enough to sit above a
        # fullscreen game's own overlay surfaces. NSPopUpMenuWindowLevel
        # (101) is what Alfred / Raycast use for their result panel —
        # it's above every regular app's window stack including
        # fullscreen Metal games, but still below system menus / dock.
        # Child dialogs spawn at the same level by default, but their
        # parent popup keeps calling ``orderFrontRegardless`` (e.g. on
        # re-show events) which would push the child back behind it —
        # so we offer ``above_overlay`` for those callers to live one
        # level higher (NSScreenSaverWindowLevel = 1000).
        target_level = (
            NSScreenSaverWindowLevel if above_overlay
            else NSPopUpMenuWindowLevel
        )
        nswindow.setLevel_(target_level)
        # orderFrontRegardless brings the window forward *without*
        # activating the app, which is exactly what we want for an
        # Accessory-policy helper: pop up over the game while leaving
        # the game in the foreground for input.
        try:
            nswindow.orderFrontRegardless()
        except Exception:
            pass
    except Exception:
        # Best-effort: if any AppKit interop fails we just leave the
        # window with stock Qt behavior. Don't blow up the UI over a
        # quality-of-life tweak.
        return


def lower_overlay_level(widget: QWidget) -> None:
    """Drop ``widget``'s NSWindow back to ``NSNormalWindowLevel``.

    Used while a child dialog (region selector) wants to claim the
    overlay layer for itself — without this, the popup's own
    high-level + ``orderFrontRegardless`` keeps re-fronting itself
    over the dialog. Pair with :func:`make_overlay_floating` once the
    dialog closes to restore the overlay behaviour.

    No-op on non-darwin / when PyObjC isn't loadable.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSNormalWindowLevel
    except Exception:
        return
    try:
        import objc  # noqa: F401
        from objc import objc_object

        nsview = objc_object(c_void_p=int(widget.winId()))  # type: ignore[arg-type]
        nswindow = nsview.window()
        if nswindow is None:
            return
        nswindow.setLevel_(NSNormalWindowLevel)
    except Exception:
        return


def apply_to_all(widgets: Iterable[QWidget]) -> None:
    for w in widgets:
        if w is not None:
            make_overlay_floating(w)


def set_accessory_app() -> None:
    """Switch the running app to ``NSApplicationActivationPolicyAccessory``.

    Default Qt apps run as ``.Regular`` — full LSUIElement=false
    behaviour: dock icon, cmd+tab participation, *and* every window
    show() ends up activating NSApp. The third one is the killer for
    a game overlay: opening a popup over HotS yanks helper to the
    foreground and the game loses focus.

    Accessory apps (the policy used by Alfred / Raycast / menu-bar
    utilities) keep their windows fully usable but never appear in
    the dock, never appear in cmd+tab, and never auto-activate the
    app when a window is shown — so the BP popup floats over the
    game without bringing the helper window itself forward.

    Called once from :func:`hots_helper.ui.app.main` before any UI
    widget is shown. No-op on non-darwin / when PyObjC isn't loadable.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import (
            NSApp,
            NSApplication,
            NSApplicationActivationPolicyAccessory,
        )
    except Exception:
        return
    try:
        # NSApp is the global instance; create it if Qt hasn't yet.
        ns_app = NSApp() if NSApp() is not None else (
            NSApplication.sharedApplication()
        )
        ns_app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        return
