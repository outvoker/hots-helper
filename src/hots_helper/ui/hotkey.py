"""Global hotkey bridge between pynput and Qt.

pynput listeners run on a background thread. We emit a Qt signal when the
hotkey fires; the signal is connected across threads so slot handlers run on
the Qt main thread (which is required for any UI work).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Signal


def _patch_pyobjc_axistrusted_lookup() -> None:
    """Workaround for a PyObjC ≥ 11 lazy-import bug on macOS.

    pynput's keyboard listener calls ``HIServices.AXIsProcessTrusted()``
    on its background thread. Recent PyObjC versions register the
    function name in ``HIServices``'s lazy table but ``get_constant``
    raises ``KeyError: 'AXIsProcessTrusted'`` on first access. The
    function lives — and is identical — in ``ApplicationServices``,
    so we eagerly import it from there and bind it onto
    ``HIServices`` before pynput touches it.

    Skipped on non-darwin and quietly no-ops if PyObjC isn't installed
    (e.g. the Windows / Linux build where pynput uses a different
    backend altogether).
    """
    if sys.platform != "darwin":
        return
    try:
        import HIServices
        import ApplicationServices
    except Exception:
        return
    if hasattr(HIServices, "_axistrusted_patched"):
        return
    fn = getattr(ApplicationServices, "AXIsProcessTrusted", None)
    if fn is None:
        return
    try:
        # Inject directly into the module so lazy ``__getattr__``
        # never fires for this name again.
        HIServices.AXIsProcessTrusted = fn
        HIServices._axistrusted_patched = True
    except Exception:
        pass


_patch_pyobjc_axistrusted_lookup()


# pynput is intentionally disabled on macOS — see HotkeyManager.set_hotkey.
# Loading the module is still cheap enough to keep here in case we ever
# want to flip the policy (e.g. to support advanced macOS users who
# turned on Accessibility access for the helper).
try:
    from pynput import keyboard
except Exception:  # pragma: no cover - platform dependent
    keyboard = None


class HotkeyManager(QObject):
    """Global hotkey bridge with platform-specific backends.

    * **macOS**: uses Carbon ``RegisterEventHotKey`` via
      :mod:`._carbon_hotkey`. No Accessibility permission required
      and it doesn't read the keyboard stream, so it never collides
      with the IME composition path that crashes pynput.
    * **Windows / Linux**: uses pynput's ``GlobalHotKeys``, which on
      Windows uses Win32 ``RegisterHotKey`` and on Linux uses the
      X record extension.

    Both backends emit ``triggered`` on the Qt main thread; the
    callbacks run inside QTimer.singleShot(0, …) trampolines so the
    Carbon event-handler frame doesn't see a long-running task.
    """

    triggered = Signal()
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._listener: "keyboard.GlobalHotKeys | None" = None
        self._carbon: "_CarbonHM | None" = None
        self._current: str = ""

    @property
    def hotkey(self) -> str:
        return self._current

    def set_hotkey(self, combo: str) -> None:
        """``combo`` in pynput form, e.g. ``<ctrl>+<shift>+h``."""
        # ----- macOS: Carbon -------------------------------------------
        if sys.platform == "darwin":
            from ._carbon_hotkey import (
                CarbonHotkeyManager as _CarbonHM,
                is_available as _carbon_available,
            )
            if not _carbon_available():
                self.error.emit("Carbon hotkey backend unavailable")
                return
            self.stop()
            if not combo:
                return
            self._carbon = _CarbonHM(self._fire)
            ok, msg = self._carbon.set_hotkey(combo)
            if ok:
                self._current = combo
            else:
                self._carbon = None
                self.error.emit(f"failed to register hotkey {combo!r}: {msg}")
            return

        # ----- Windows / Linux: pynput ---------------------------------
        if keyboard is None:
            self.error.emit("pynput not available; global hotkeys disabled")
            return
        self.stop()
        if not combo:
            return
        try:
            listener = keyboard.GlobalHotKeys({combo: self._fire})
            listener.start()
            self._listener = listener
            self._current = combo
        except Exception as e:
            self.error.emit(f"failed to register hotkey {combo!r}: {e}")

    def _fire(self) -> None:
        self.triggered.emit()

    def stop(self) -> None:
        if self._carbon is not None:
            try:
                self._carbon.stop()
            except Exception:
                pass
            self._carbon = None
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._current = ""
