"""Global hotkey bridge between pynput and Qt.

pynput listeners run on a background thread. We emit a Qt signal when the
hotkey fires; the signal is connected across threads so slot handlers run on
the Qt main thread (which is required for any UI work).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

try:
    from pynput import keyboard
except Exception:  # pragma: no cover - platform dependent
    keyboard = None


class HotkeyManager(QObject):
    triggered = Signal()
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._listener: "keyboard.GlobalHotKeys | None" = None
        self._current: str = ""

    @property
    def hotkey(self) -> str:
        return self._current

    def set_hotkey(self, combo: str) -> None:
        """``combo`` in pynput form, e.g. ``<ctrl>+<shift>+h``."""
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
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
            self._current = ""
