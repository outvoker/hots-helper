"""Modal-ish progress dialog shown while the hotkey OCR pipeline runs.

The screenshot + OCR + DB-lookup chain takes ~1-3 s on Windows; on a
slow machine the user sees nothing until the BP popup pops, which feels
like the app froze. This dialog gives them something to watch.

Design notes
------------
* Frameless, always-on-top, ``WA_ShowWithoutActivating`` — same focus
  rules as the BP popup so it never minimises a fullscreen game.
* The "stages" list is intentionally a bit overblown ("Scanning squad
  history…", "Computing Wilson confidence intervals…"). The work is real
  but the marketing copy makes the wait feel like the app is *doing*
  something instead of waiting on Windows OCR.
* We don't drive the steps from the worker — the worker emits raw
  ``progress`` strings, which is great for logs but too noisy for a
  status line. Instead we rotate through a script on a timer; the
  timer just keeps stepping forward, and ``finish()`` collapses to
  the success state regardless of which stage we were on.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QPropertyAnimation,
    QTimer,
    Qt,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t
from .theme import (
    BG_DEEP,
    BG_ELEVATED,
    GOLD,
    GOLD_BRIGHT,
    GOLD_DIM,
    LINE,
    TEXT,
    TEXT_DIM,
)


# Step copy keys — one i18n key per stage. The worker takes ~1.5–3 s
# total, so we want roughly 4–6 messages spaced ~400ms apart.
_STEP_KEYS = (
    "ui.capture.step_capture",
    "ui.capture.step_ocr",
    "ui.capture.step_parse",
    "ui.capture.step_resolve",
    "ui.capture.step_score",
    "ui.capture.step_render",
)


class CaptureProgressDialog(QWidget):
    """Frameless centered progress card. Call ``start()`` to show, then
    ``finish()`` (success or error) when the worker completes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setObjectName("captureRoot")
        self.setStyleSheet(
            f"QWidget#captureRoot {{"
            f" background: {BG_DEEP};"
            f" border: 1px solid {GOLD_DIM};"
            f" border-radius: 12px;"
            f"}}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 16, 22, 16)
        outer.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        spinner = QLabel("⚡")
        spinner.setStyleSheet(f"color: {GOLD_BRIGHT}; font-size: 20pt;")
        title_row.addWidget(spinner)
        title = QLabel(t("ui.capture.title"))
        title.setStyleSheet(
            f"color: {GOLD}; font-size: 14pt; font-weight: 700;"
            f" letter-spacing: 0.5px;"
        )
        title_row.addWidget(title, 1)
        outer.addLayout(title_row)

        self._step_label = QLabel(t(_STEP_KEYS[0]))
        self._step_label.setStyleSheet(
            f"color: {TEXT}; font-size: 11pt; padding: 2px 0;"
        )
        self._step_label.setWordWrap(True)
        self._step_label.setMinimumWidth(420)
        outer.addWidget(self._step_label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # busy/indeterminate
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {BG_ELEVATED}; border: 1px solid {LINE};"
            f" border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: qlineargradient("
            f"  x1:0, y1:0, x2:1, y2:0,"
            f"  stop:0 {GOLD_DIM}, stop:0.5 {GOLD_BRIGHT}, stop:1 {GOLD_DIM});"
            f" border-radius: 2px; }}"
        )
        outer.addWidget(self._bar)

        # Subtle "what we're doing under the hood" line — flashy enough to
        # make the user think the app is grinding hard for them, while the
        # underlying work (winrt OCR + a couple of SQL queries) is real.
        self._sub_label = QLabel("")
        self._sub_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 9pt; font-style: italic;"
        )
        self._sub_label.setWordWrap(True)
        outer.addWidget(self._sub_label)

        self.setFixedWidth(480)
        self.adjustSize()

        # Step rotation timer.
        self._step_index = 0
        self._timer = QTimer(self)
        self._timer.setInterval(420)  # ms; ≈ a fresh step every half-sec
        self._timer.timeout.connect(self._tick)

        # Subtle fade-in for first-show.
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(180)

    # --- public API ---------------------------------------------------------

    def start(self, anchor: QWidget | None = None) -> None:
        """Show the dialog, centered above ``anchor`` (the main window)
        or on the primary screen if no anchor is given."""
        self._step_index = 0
        self._step_label.setText(t(_STEP_KEYS[0]))
        self._sub_label.setText(t("ui.capture.sub_first"))
        self._bar.setRange(0, 0)
        if anchor is not None:
            ctr = anchor.frameGeometry().center()
        else:
            scr = QGuiApplication.primaryScreen()
            ctr = scr.availableGeometry().center()
        geo = self.frameGeometry()
        geo.moveCenter(ctr)
        self.move(geo.topLeft())
        self.setWindowOpacity(0.0)
        self.show()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start()

    def update_substatus(self, msg: str) -> None:
        """Plumb the worker's raw progress string into the subtle bottom
        line. The main step label keeps marching forward on the timer."""
        if msg:
            self._sub_label.setText(msg)

    def finish(self, ok: bool = True, message: str | None = None) -> None:
        self._timer.stop()
        if ok:
            self._step_label.setText(t("ui.capture.done"))
            self._bar.setRange(0, 1)
            self._bar.setValue(1)
        else:
            self._step_label.setText(message or t("ui.capture.failed"))
            self._sub_label.setText("")
        # Fade out shortly after.
        QTimer.singleShot(380 if ok else 1800, self._fade_out_then_hide)

    def _fade_out_then_hide(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._after_fade)
        self._fade.start()

    def _after_fade(self) -> None:
        try:
            self._fade.finished.disconnect(self._after_fade)
        except (TypeError, RuntimeError):
            pass
        self.hide()

    def _tick(self) -> None:
        # Move forward, pin at the last step (we keep showing it until
        # the worker actually finishes — feels worse to flap back to
        # an earlier message than to linger on the last one).
        self._step_index = min(self._step_index + 1, len(_STEP_KEYS) - 1)
        self._step_label.setText(t(_STEP_KEYS[self._step_index]))
