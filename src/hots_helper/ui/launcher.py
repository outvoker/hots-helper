"""Tiny always-on-top floating launcher.

Lives independently of the main window; sits on top of the game so the
user can click an action button without having to alt-tab back to the
helper. We use this as the primary input surface on Windows because
HotS / Battle.net often run elevated, and Windows UIPI silently drops
keyboard events from a non-elevated process to an elevated one. Mouse
clicks on a top-most window aren't subject to that filter, so the
launcher works regardless of admin parity.

Layout:

* **Collapsed** — a single round chip with the app icon. Drag to move.
* **Expanded** — three action buttons fan out horizontally:
    1. BP 智能分析  (calls the same handler the BP hotkey does)
    2. 公屏翻译     (chat OCR + translate)
    3. 中文转译     (compose-to-target popup)
* After ``COLLAPSE_TIMEOUT_MS`` of idle (no hover, no click) the
  launcher auto-collapses again.

Position is persisted via two new ``Config`` fields so the chip stays
where the user dragged it across restarts.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..i18n import on_change as on_lang_change, t
from .assets import app_icon
from .theme import (
    BG_DEEP,
    BG_ELEVATED,
    BG_INPUT,
    GOLD,
    GOLD_BRIGHT,
    GOLD_DIM,
    LINE,
    TEXT,
    TEXT_DIM,
)


# Auto-collapse delay after the last user interaction.
COLLAPSE_TIMEOUT_MS = 4000

# Sizes — kept small so the chip doesn't dominate the game view.
_CHIP_SIZE = 44
_BTN_HEIGHT = 36


class FloatingLauncher(QWidget):
    """Always-on-top, focus-safe round chip that expands into a row of
    action buttons. Reuses the BP popup's frameless / non-activating
    flag set so it never drops a fullscreen game out of exclusive mode.
    """

    def __init__(
        self,
        config: Config,
        *,
        on_bp: Callable[[], None],
        on_chat_translate: Callable[[], None],
        on_compose_translate: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_bp = on_bp
        self._on_chat = on_chat_translate
        self._on_compose = on_compose_translate

        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # Vertical stack: round chip on top, expansion panel underneath.
        # Buttons drop down from the chip rather than fanning out to
        # the right — keeps the launcher's horizontal footprint small
        # so it doesn't run off the side of the screen when the chip
        # is parked near the right edge.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.setAlignment(Qt.AlignHCenter)

        self._chip = QToolButton()
        self._chip.setFixedSize(_CHIP_SIZE, _CHIP_SIZE)
        self._chip.setIcon(app_icon())
        self._chip.setIconSize(QSize(28, 28))
        self._chip.setStyleSheet(
            f"QToolButton {{"
            f" background: qradialgradient(cx:0.5, cy:0.4, radius:0.7,"
            f"   stop:0 {BG_ELEVATED}, stop:1 {BG_DEEP});"
            f" border: 1px solid {GOLD_DIM};"
            f" border-radius: {_CHIP_SIZE // 2}px;"
            f"}}"
            f"QToolButton:hover {{ border: 1px solid {GOLD};"
            f" background: qradialgradient(cx:0.5, cy:0.4, radius:0.7,"
            f"   stop:0 {BG_INPUT}, stop:1 {BG_DEEP}); }}"
            f"QToolButton:pressed {{ border: 1px solid {GOLD_BRIGHT}; }}"
        )
        self._chip.installEventFilter(self)
        self._chip.clicked.connect(self._toggle_expanded)
        outer.addWidget(self._chip, alignment=Qt.AlignHCenter)

        # Expansion panel — a vertical column of action buttons in a
        # gold-edged rounded card. Sits directly under the chip.
        self._expand_panel = QFrame()
        self._expand_panel.setObjectName("launcherExpand")
        self._expand_panel.setStyleSheet(
            f"QFrame#launcherExpand {{"
            f" background: {BG_DEEP};"
            f" border: 1px solid {GOLD_DIM};"
            f" border-radius: 10px;"
            f"}}"
        )
        col = QVBoxLayout(self._expand_panel)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(4)

        self._bp_btn = self._make_action_btn("ui.launcher.bp")
        self._bp_btn.clicked.connect(self._fire_bp)
        col.addWidget(self._bp_btn)

        self._chat_btn = self._make_action_btn("ui.launcher.chat")
        self._chat_btn.clicked.connect(self._fire_chat)
        col.addWidget(self._chat_btn)

        self._compose_btn = self._make_action_btn("ui.launcher.compose")
        self._compose_btn.clicked.connect(self._fire_compose)
        col.addWidget(self._compose_btn)

        outer.addWidget(self._expand_panel, alignment=Qt.AlignHCenter)
        self._expand_panel.hide()

        # Drag state for the chip.
        self._drag_origin: QPoint | None = None
        self._dragging = False

        # Auto-collapse timer.
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)

        # Re-translate when the user changes locale.
        self._retranslate()
        on_lang_change(lambda _c: self._retranslate())

        self.adjustSize()
        self._restore_position()

    # --- public API ---------------------------------------------------------

    def show_at_default_corner(self) -> None:
        """Place the launcher in the top-right of the primary screen.
        Used the first time the app runs (no saved position yet)."""
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 24, scr.top() + 80)

    # --- visual state -------------------------------------------------------

    def _retranslate(self) -> None:
        self._chip.setToolTip(t("ui.launcher.tooltip"))
        self._bp_btn.setText(t("ui.launcher.bp"))
        self._chat_btn.setText(t("ui.launcher.chat"))
        self._compose_btn.setText(t("ui.launcher.compose"))
        # Sizes change with text length on locale switch.
        self.adjustSize()

    def _make_action_btn(self, label_key: str) -> QPushButton:
        b = QPushButton(t(label_key))
        b.setMinimumHeight(_BTN_HEIGHT)
        b.setStyleSheet(
            f"QPushButton {{"
            f" background: {BG_INPUT}; color: {TEXT};"
            f" border: 1px solid {LINE}; border-radius: {_BTN_HEIGHT // 2}px;"
            f" padding: 4px 14px; font-size: 10pt; font-weight: 600;"
            f"}}"
            f"QPushButton:hover {{ background: {BG_ELEVATED};"
            f" border-color: {GOLD_DIM}; color: {GOLD_BRIGHT}; }}"
            f"QPushButton:pressed {{ background: {BG_DEEP};"
            f" border-color: {GOLD}; color: {GOLD_BRIGHT}; }}"
        )
        return b

    def _toggle_expanded(self) -> None:
        if self._expand_panel.isVisible():
            self._collapse()
        else:
            self._expand()

    def _expand(self) -> None:
        self._expand_panel.show()
        self.adjustSize()
        self._restart_collapse_timer()

    def _collapse(self) -> None:
        self._expand_panel.hide()
        self.adjustSize()
        self._collapse_timer.stop()

    def _restart_collapse_timer(self) -> None:
        self._collapse_timer.start(COLLAPSE_TIMEOUT_MS)

    # --- action wiring ------------------------------------------------------

    def _fire_bp(self) -> None:
        self._collapse()
        self._on_bp()

    def _fire_chat(self) -> None:
        self._collapse()
        self._on_chat()

    def _fire_compose(self) -> None:
        self._collapse()
        self._on_compose()

    # --- drag-to-move on the chip ------------------------------------------

    def eventFilter(self, obj, ev):  # type: ignore[no-untyped-def]
        if obj is not self._chip:
            return super().eventFilter(obj, ev)
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._drag_origin = (
                ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._dragging = False
            return False  # let the click reach QToolButton too
        if ev.type() == QEvent.MouseMove and ev.buttons() & Qt.LeftButton:
            if self._drag_origin is not None:
                delta = (
                    ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                ) - self._drag_origin
                if abs(delta.x()) + abs(delta.y()) > 4:
                    self._dragging = True
                    self.move(ev.globalPosition().toPoint() - self._drag_origin)
            return self._dragging  # swallow only when we're actually dragging
        if ev.type() == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            origin = self._drag_origin
            was_dragging = self._dragging
            self._drag_origin = None
            self._dragging = False
            if was_dragging:
                self._save_position()
                # Suppress the synthetic click that QToolButton would
                # otherwise emit at the end of a drag.
                return True
        return super().eventFilter(obj, ev)

    # --- enter/leave keep expanded mode alive ------------------------------

    def enterEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if self._expand_panel.isVisible():
            self._collapse_timer.stop()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if self._expand_panel.isVisible():
            self._restart_collapse_timer()
        super().leaveEvent(ev)

    # --- persistence -------------------------------------------------------

    def _restore_position(self) -> None:
        if self._config.launcher_x < 0 or self._config.launcher_y < 0:
            self.show_at_default_corner()
            return
        # Clamp so a saved position from a different monitor layout
        # doesn't leave the chip off-screen.
        scr = QGuiApplication.primaryScreen().availableGeometry()
        x = max(scr.left(), min(self._config.launcher_x, scr.right() - self.width()))
        y = max(scr.top(), min(self._config.launcher_y, scr.bottom() - self.height()))
        self.move(x, y)

    def _save_position(self) -> None:
        pos = self.frameGeometry().topLeft()
        self._config.launcher_x = pos.x()
        self._config.launcher_y = pos.y()
        try:
            self._config.save()
        except Exception:
            # Position is a quality-of-life feature; never let a save
            # failure crash the launcher.
            pass
