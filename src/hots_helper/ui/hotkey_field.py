"""Display-and-edit hotkey field used on the main window.

Replaces a bare ``QKeySequenceEdit`` with a two-state widget:

* **Display state** — shows the current shortcut as a plain styled label.
  Clicks elsewhere on the window do nothing; the user has to explicitly
  press *编辑* to start recording.
* **Edit state** — swaps in a real ``QKeySequenceEdit`` and grabs focus,
  the *编辑* button becomes *保存*.  Pressing *保存* (or Enter inside
  the recorder) emits :pyattr:`saved` with the new pynput-form combo
  string.  *取消* reverts to the original.

Without this gating, the BP-card hotkey field on the main window
"records" any keystroke the user makes while the field is focused,
which silently overwrites their saved hotkey when they're just trying
to type elsewhere.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from ..i18n import t
from .theme import BG_INPUT, GOLD, GOLD_BRIGHT, GOLD_DIM, LINE, TEXT, TEXT_DIM


class HotkeyField(QWidget):
    """Compact "display + edit" hotkey editor.

    The widget owns its UI state. Wire up :pyattr:`saved` to persist the
    new combo and re-register it with the global hotkey manager. Wire
    up :pyattr:`canceled` if you need to react to the user backing out
    (most callers don't).
    """

    saved = Signal(str)       # pynput-form combo, e.g. "<ctrl>+<shift>+t"
    canceled = Signal()

    def __init__(
        self,
        initial: str,
        *,
        qt_from_pynput: Callable[[str], QKeySequence],
        pynput_from_qt: Callable[[QKeySequence], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._qt_from_pynput = qt_from_pynput
        self._pynput_from_qt = pynput_from_qt
        self._current = initial

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Stacked widget: display label vs. the live recorder. Saves
        # juggling visibility manually and ensures only one of the two
        # ever has the focus chain.
        self._stack = QStackedWidget()
        self._display = QLabel()
        self._display.setStyleSheet(
            f"QLabel {{"
            f" background: {BG_INPUT}; color: {TEXT};"
            f" border: 1px solid {LINE}; border-radius: 4px;"
            f" padding: 4px 10px; min-height: 22px;"
            f"}}"
        )
        self._editor = QKeySequenceEdit()
        self._stack.addWidget(self._display)
        self._stack.addWidget(self._editor)
        layout.addWidget(self._stack, 1)

        self._edit_btn = QPushButton()
        self._edit_btn.clicked.connect(self._on_edit_clicked)
        layout.addWidget(self._edit_btn)

        self._cancel_btn = QPushButton()
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        self._cancel_btn.setVisible(False)
        layout.addWidget(self._cancel_btn)

        self._refresh_display()
        self._retranslate()

    # --- public API --------------------------------------------------------

    def set_combo(self, combo: str) -> None:
        """Force the displayed shortcut to ``combo`` (pynput form). Called
        when the parent widget reverts a save (e.g. validation failed)."""
        self._current = combo
        self._refresh_display()
        self._exit_edit_mode()

    def retranslate(self) -> None:
        self._retranslate()

    # --- internals ---------------------------------------------------------

    def _retranslate(self) -> None:
        if self._stack.currentIndex() == 0:
            self._edit_btn.setText(t("ui.hotkey.edit"))
        else:
            self._edit_btn.setText(t("ui.hotkey.save"))
        self._cancel_btn.setText(t("ui.hotkey.cancel"))

    def _refresh_display(self) -> None:
        if self._current:
            seq = self._qt_from_pynput(self._current)
            self._display.setText(seq.toString() or self._current)
            self._display.setStyleSheet(
                f"QLabel {{"
                f" background: {BG_INPUT}; color: {TEXT};"
                f" border: 1px solid {LINE}; border-radius: 4px;"
                f" padding: 4px 10px; min-height: 22px;"
                f"}}"
            )
        else:
            self._display.setText(t("ui.hotkey.unset"))
            self._display.setStyleSheet(
                f"QLabel {{"
                f" background: {BG_INPUT}; color: {TEXT_DIM};"
                f" font-style: italic;"
                f" border: 1px solid {LINE}; border-radius: 4px;"
                f" padding: 4px 10px; min-height: 22px;"
                f"}}"
            )

    def _on_edit_clicked(self) -> None:
        if self._stack.currentIndex() == 0:
            # Enter edit mode — preload the recorder with the current
            # shortcut, swap, focus, change the button to "save".
            self._editor.setKeySequence(self._qt_from_pynput(self._current))
            self._stack.setCurrentIndex(1)
            self._editor.setFocus(Qt.OtherFocusReason)
            self._cancel_btn.setVisible(True)
            self._edit_btn.setText(t("ui.hotkey.save"))
            # Subtle visual hint that the field is "armed".
            self._editor.setStyleSheet(
                f"QKeySequenceEdit {{"
                f" background: {BG_INPUT}; color: {GOLD_BRIGHT};"
                f" border: 1px solid {GOLD}; border-radius: 4px;"
                f" padding: 4px 10px;"
                f"}}"
            )
            return
        # Save: read the recorded sequence, validate, emit.
        seq = self._editor.keySequence()
        combo = self._pynput_from_qt(seq) if not seq.isEmpty() else ""
        if not combo:
            # Empty save = treat as cancel (user clicked save without
            # actually pressing a key combo).
            self._on_cancel_clicked()
            return
        self._current = combo
        self._refresh_display()
        self._exit_edit_mode()
        self.saved.emit(combo)

    def _on_cancel_clicked(self) -> None:
        self._exit_edit_mode()
        self.canceled.emit()

    def _exit_edit_mode(self) -> None:
        self._stack.setCurrentIndex(0)
        self._cancel_btn.setVisible(False)
        self._edit_btn.setText(t("ui.hotkey.edit"))
        # Clear the recorder so a stale half-recorded sequence doesn't
        # show next time the user clicks 编辑.
        self._editor.clear()
