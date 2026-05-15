"""Floating popups for the two translation hotkeys.

* :class:`ChatTranslationPopup` — shows the result of an in-game
  chat-OCR + translate run. Lines on the left, Chinese on the right,
  click any row to copy the Chinese to the clipboard.
* :class:`ComposeTranslatePopup` — small input box: the user types
  Chinese, picks a target language, gets the translation back ready
  to copy/paste into the in-game chat.

Both reuse the same focus rules as the BP popup (Qt.Tool +
WindowStaysOnTopHint + WA_ShowWithoutActivating) so they float over
the game without ever stealing focus / dropping fullscreen.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QObject,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t
from ..translate import SUPPORTED_LANGS
from .theme import (
    BG_DEEP,
    BG_ELEVATED,
    BG_HOVER,
    BG_INPUT,
    GOLD,
    GOLD_BRIGHT,
    GOLD_DIM,
    LINE,
    TEXT,
    TEXT_DIM,
)
from .workers import (
    ChatTranslationResult,
    ComposeTranslateWorker,
    ComposeTranslationResult,
)


def _frameless_floating(window: QWidget) -> None:
    """Apply the standard frameless / always-on-top / non-focus-stealing
    flags shared by every translation popup."""
    window.setWindowFlags(
        Qt.Tool
        | Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.NoDropShadowWindowHint
    )
    window.setAttribute(Qt.WA_ShowWithoutActivating, True)
    window.setAttribute(Qt.WA_TranslucentBackground, False)


def _gold_card_qss(object_name: str) -> str:
    """Shared frameless card chrome — gold-edged, rounded, dark."""
    return (
        f"QWidget#{object_name} {{"
        f" background: {BG_DEEP};"
        f" border: 1px solid {GOLD_DIM};"
        f" border-radius: 12px;"
        f"}}"
    )


# === Chat OCR + translate popup ============================================


class ChatTranslationPopup(QWidget):
    """Renders the result of one chat-OCR run.

    Each row: original on the left, zh translation on the right, with
    a small "复制" button that puts the zh text on the clipboard so the
    user can paste it into Discord / their notes / etc. (We deliberately
    do *not* paste into the game — the in-game chat box doesn't always
    accept clipboard, and we don't want to fiddle with sendkeys.)
    """

    def __init__(self) -> None:
        super().__init__()
        _frameless_floating(self)
        self.setObjectName("chatTransRoot")
        self.setStyleSheet(_gold_card_qss("chatTransRoot"))
        self.setMinimumWidth(560)

        # Last screenshot we showed translations for. Stashed so the
        # "redraw chat region" button can re-OCR the same image after
        # the user drags a tighter rectangle. None means there's no
        # frozen result on screen yet (or the worker errored before
        # taking a screenshot).
        self._screenshot_path: Path | None = None
        # Worker for the redraw → re-translate flow. Same machinery as
        # the main capture worker, but reused — we keep one outstanding
        # at a time and disable the redraw button while it's running.
        self._redraw_thread: QThread | None = None
        self._redraw_worker = None
        self._redraw_busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 12, 18, 14)
        outer.setSpacing(10)

        # Header row: title + redraw button + close button.
        head = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet(
            f"color: {GOLD}; font-size: 14pt; font-weight: 700;"
            f" letter-spacing: 0.5px;"
        )
        head.addWidget(self.title_label)
        head.addStretch(1)
        self.redraw_btn = QPushButton()
        self.redraw_btn.clicked.connect(self._on_redraw_clicked)
        head.addWidget(self.redraw_btn)
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.clicked.connect(self.hide)
        self.close_btn.setStyleSheet(
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:14px;"
            f" font-weight:bold; padding:0; }}"
            f"QPushButton:hover {{ color:#e08585;"
            f" border-color:#e08585; background:{BG_DEEP}; }}"
        )
        head.addWidget(self.close_btn)
        outer.addLayout(head)

        self.subtitle = QLabel()
        self.subtitle.setStyleSheet(f"color:{TEXT_DIM}; font-size:9pt;")
        self.subtitle.setWordWrap(True)
        outer.addWidget(self.subtitle)

        # Scrollable rows.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self._rows = QVBoxLayout(body)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(6)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._drag_pos = None
        self._retranslate()

    def _retranslate(self) -> None:
        self.title_label.setText(t("ui.chat_trans.title"))
        self.subtitle.setText(t("ui.chat_trans.subtitle"))
        self.redraw_btn.setText(t("ui.chat_trans.redraw"))
        self.redraw_btn.setToolTip(t("ui.chat_trans.redraw_tip"))

    # --- public API ---------------------------------------------------------

    def show_result(self, result: ChatTranslationResult) -> None:
        # Stash the screenshot so the "redraw region" button has
        # something to re-OCR. Even if the result has no chat lines,
        # the user might still want to redraw and try a tighter box.
        self._screenshot_path = result.screenshot_path
        self.redraw_btn.setEnabled(self._screenshot_path is not None)

        # Clear old rows.
        while self._rows.count():
            item = self._rows.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if result.error:
            err = QLabel(result.error)
            err.setStyleSheet(f"color:#e08585;")
            err.setWordWrap(True)
            self._rows.addWidget(err)
        elif not result.pairs:
            empty = QLabel(t("ui.chat_trans.empty"))
            empty.setStyleSheet(f"color:{TEXT_DIM}; font-style:italic;")
            empty.setWordWrap(True)
            self._rows.addWidget(empty)
        else:
            for (orig, zh), src in zip(
                result.pairs,
                result.detected_sources or [""] * len(result.pairs),
            ):
                self._rows.addWidget(self._make_row(orig, zh, src))
        self._rows.addStretch(1)

        # Show centered on the primary screen so the popup doesn't land
        # over the chat box itself (and since we never activate, no
        # game minimisation risk).
        self.adjustSize()
        scr = QGuiApplication.primaryScreen().availableGeometry()
        geo = self.frameGeometry()
        geo.moveCenter(scr.center())
        # Nudge up so we don't sit right on top of where chat usually is.
        geo.moveTop(scr.top() + int(scr.height() * 0.18))
        self.move(geo.topLeft())
        self.show()
        self.raise_()

    def _make_row(self, orig: str, zh: str, src: str) -> QWidget:
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background:{BG_ELEVATED}; border:1px solid {LINE};"
            f" border-radius: 6px; }}"
            f"QFrame:hover {{ border-color: {GOLD_DIM}; }}"
        )
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(0)
        src_tag = QLabel(src.upper() if src else "?")
        src_tag.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:8pt;"
            f" letter-spacing:1px; font-weight:600;"
        )
        left.addWidget(src_tag)
        orig_label = QLabel(orig)
        orig_label.setWordWrap(True)
        orig_label.setStyleSheet(f"color:{TEXT}; font-size:11pt;")
        left.addWidget(orig_label)
        h.addLayout(left, 1)

        # Right side: translation + copy button.
        right = QVBoxLayout()
        right.setSpacing(2)
        zh_label = QLabel(zh)
        zh_label.setWordWrap(True)
        zh_label.setStyleSheet(
            f"color:{GOLD_BRIGHT}; font-size:11pt; font-weight:600;"
        )
        right.addWidget(zh_label)
        copy_btn = QPushButton(t("ui.chat_trans.copy"))
        copy_btn.setFixedHeight(22)
        copy_btn.clicked.connect(lambda: self._copy_to_clipboard(zh))
        right.addWidget(copy_btn, alignment=Qt.AlignRight)
        h.addLayout(right, 1)
        return row

    def _copy_to_clipboard(self, text: str) -> None:
        cb = QApplication.clipboard()
        cb.setText(text)

    # --- redraw region flow -------------------------------------------------

    def _on_redraw_clicked(self) -> None:
        if self._redraw_busy:
            return
        if not self._screenshot_path:
            QMessageBox.information(
                self,
                t("ui.popup.region.no_screenshot_title"),
                t("ui.popup.region.no_screenshot_body"),
            )
            return
        # Open the same RegionSelectorDialog the BP popup uses for
        # per-name corrections. We translate the picked rectangle
        # (image-pixel coordinates) into a re-OCR + re-translate run.
        from .region_select import RegionSelectorDialog
        try:
            dlg = RegionSelectorDialog(self._screenshot_path, parent=self)
        except Exception as e:
            QMessageBox.warning(
                self,
                t("ui.popup.region.cannot_open"),
                str(e),
            )
            return
        dlg.region_picked.connect(self._on_region_picked)
        dlg.exec()

    def _on_region_picked(self, x: int, y: int, w: int, h: int) -> None:
        if not self._screenshot_path:
            return
        # Re-OCR + re-translate on a worker thread so the network call
        # doesn't block the popup. Use a small custom worker
        # (RedrawTranslateWorker) defined in this module — full screen
        # capture is unnecessary, we already have the image and the box.
        self._redraw_busy = True
        self.redraw_btn.setEnabled(False)
        self.subtitle.setText(t("ui.chat_trans.redrawing"))
        thread = QThread(self)
        worker = _RedrawTranslateWorker(
            screenshot_path=self._screenshot_path,
            x=x, y=y, w=w, h=h,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_redraw_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_redraw)
        self._redraw_thread = thread
        self._redraw_worker = worker
        thread.start()

    def _cleanup_redraw(self) -> None:
        if self._redraw_worker is not None:
            self._redraw_worker.deleteLater()
            self._redraw_worker = None
        if self._redraw_thread is not None:
            self._redraw_thread.deleteLater()
            self._redraw_thread = None
        self._redraw_busy = False
        self.redraw_btn.setEnabled(self._screenshot_path is not None)
        self._retranslate()  # restore subtitle

    def _on_redraw_finished(self, result: ChatTranslationResult) -> None:
        # Reuse show_result — it already clears the old rows and
        # paints the new ones.
        self.show_result(result)

    # --- frameless drag -----------------------------------------------------

    def mousePressEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.button() == Qt.LeftButton:
            self._drag_pos = (
                ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)
            ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        self._drag_pos = None


# === Redraw worker ==========================================================


class _RedrawTranslateWorker(QObject):
    """Re-OCR a user-cropped rectangle of an existing screenshot,
    filter to chat-shaped text, translate to Chinese, return.

    Smaller cousin of :class:`ChatTranslateWorker` — skips the screen-
    capture stage and uses the manual crop as the input image. The
    finished signal carries the same ``ChatTranslationResult`` shape
    so the popup can reuse :meth:`show_result` unchanged.
    """

    progress = Signal(str)
    finished = Signal(object)  # ChatTranslationResult

    def __init__(
        self,
        screenshot_path: Path,
        x: int, y: int, w: int, h: int,
    ) -> None:
        super().__init__()
        self._path = screenshot_path
        self._x = x
        self._y = y
        self._w = w
        self._h = h

    def run(self) -> None:
        import tempfile
        import traceback
        from PIL import Image

        from ..chat_ocr import filter_chat_blocks
        from ..ocr import recognize
        from ..translate import TranslateError, translate
        from .workers import ChatTranslationResult

        log_lines: list[str] = []
        # 1. Crop
        try:
            with Image.open(self._path) as im:
                crop = im.crop(
                    (self._x, self._y, self._x + self._w, self._y + self._h)
                )
                pad = 8
                padded = Image.new(
                    "RGB",
                    (crop.width + 2 * pad, crop.height + 2 * pad),
                    (0, 0, 0),
                )
                padded.paste(crop, (pad, pad))
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    tmp_path = Path(f.name)
                padded.save(tmp_path)
        except Exception as e:
            log_lines.append(f"[crop error] {type(e).__name__}: {e}")
            log_lines.append(traceback.format_exc())
            self.finished.emit(ChatTranslationResult(
                screenshot_path=self._path,
                error=f"裁剪失败：{e}",
                log_lines=log_lines,
            ))
            return

        # 2. OCR the crop.
        try:
            blocks = recognize(tmp_path)
        except Exception as e:
            log_lines.append(f"[OCR error] {type(e).__name__}: {e}")
            self.finished.emit(ChatTranslationResult(
                screenshot_path=self._path,
                error=f"OCR 失败：{e}",
                log_lines=log_lines,
            ))
            return
        finally:
            tmp_path.unlink(missing_ok=True)

        chat = filter_chat_blocks(blocks)
        if not chat:
            self.finished.emit(ChatTranslationResult(
                screenshot_path=self._path,
                pairs=[],
                log_lines=log_lines + ["[redraw] no chat-shaped text in crop"],
            ))
            return

        # 3. Translate.
        try:
            results = translate(
                [c.text for c in chat],
                target="zh",
                source="auto",
            )
        except TranslateError as e:
            self.finished.emit(ChatTranslationResult(
                screenshot_path=self._path,
                error=f"翻译失败：{e}",
                log_lines=log_lines,
            ))
            return

        pairs = [(c.text, r.text) for c, r in zip(chat, results)]
        sources = [r.detected_source for r in results]
        self.finished.emit(ChatTranslationResult(
            screenshot_path=self._path,
            pairs=pairs,
            detected_sources=sources,
            log_lines=log_lines + [f"[redraw] {len(pairs)} lines translated"],
        ))


# === Compose popup =========================================================


class ComposeTranslatePopup(QWidget):
    """Tiny input box → translate Chinese to target language → display.

    Three controls: input text edit (top), target-language combo + send
    button (middle), translated output read-only label + copy button
    (bottom).  Pressing Enter in the input box also fires the translate
    request.
    """

    def __init__(self) -> None:
        super().__init__()
        _frameless_floating(self)
        self.setObjectName("composeRoot")
        self.setStyleSheet(_gold_card_qss("composeRoot"))
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 12, 18, 14)
        outer.setSpacing(8)

        head = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet(
            f"color:{GOLD}; font-size:14pt; font-weight:700;"
            f" letter-spacing:0.5px;"
        )
        head.addWidget(self.title_label)
        head.addStretch(1)
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.clicked.connect(self.hide)
        self.close_btn.setStyleSheet(
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:14px;"
            f" font-weight:bold; padding:0; }}"
            f"QPushButton:hover {{ color:#e08585;"
            f" border-color:#e08585; background:{BG_DEEP}; }}"
        )
        head.addWidget(self.close_btn)
        outer.addLayout(head)

        self.input = QPlainTextEdit()
        self.input.setFixedHeight(80)
        self.input.installEventFilter(self)
        outer.addWidget(self.input)

        # Target row.
        target_row = QHBoxLayout()
        self.target_label = QLabel()
        target_row.addWidget(self.target_label)
        self.target_combo = QComboBox()
        for code, label in SUPPORTED_LANGS:
            if code == "zh":
                continue  # don't translate zh→zh
            self.target_combo.addItem(label, code)
        target_row.addWidget(self.target_combo, 1)
        self.send_btn = QPushButton()
        self.send_btn.clicked.connect(self._send)
        self.send_btn.setProperty("variant", "primary")
        target_row.addWidget(self.send_btn)
        outer.addLayout(target_row)

        # Output.
        self.output = QLabel()
        self.output.setWordWrap(True)
        self.output.setMinimumHeight(48)
        self.output.setStyleSheet(
            f"color:{GOLD_BRIGHT}; font-size:12pt; font-weight:600;"
            f" background:{BG_ELEVATED}; border:1px solid {LINE};"
            f" border-radius:6px; padding:8px 10px;"
        )
        self.output.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.output)

        self.copy_btn = QPushButton()
        self.copy_btn.clicked.connect(self._copy_output)
        outer.addWidget(self.copy_btn, alignment=Qt.AlignRight)

        self._drag_pos = None
        self._busy = False
        self._thread: QThread | None = None
        self._worker: ComposeTranslateWorker | None = None
        self._retranslate()

    def _retranslate(self) -> None:
        self.title_label.setText(t("ui.compose_trans.title"))
        self.target_label.setText(t("ui.compose_trans.target"))
        self.send_btn.setText(t("ui.compose_trans.send"))
        self.copy_btn.setText(t("ui.chat_trans.copy"))
        self.input.setPlaceholderText(t("ui.compose_trans.input_placeholder"))
        self.send_btn.style().unpolish(self.send_btn)
        self.send_btn.style().polish(self.send_btn)

    # --- public API ---------------------------------------------------------

    def open_centered(self) -> None:
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        geo = self.frameGeometry()
        geo.moveCenter(scr.center())
        self.move(geo.topLeft())
        self.show()
        self.raise_()
        # Focus the input *only if* the user explicitly asked for this
        # popup — same focus-stealing risk as the BP popup, but the
        # user just pressed a hotkey so they expect to be typing now.
        self.input.setFocus()

    # --- internal -----------------------------------------------------------

    def eventFilter(self, obj, ev):  # type: ignore[no-untyped-def]
        # Enter in the input fires the translate. Shift+Enter for newline.
        from PySide6.QtCore import QEvent
        if obj is self.input and ev.type() == QEvent.KeyPress:
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                ev.modifiers() & Qt.ShiftModifier
            ):
                self._send()
                return True
        return super().eventFilter(obj, ev)

    def _send(self) -> None:
        if self._busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        target = self.target_combo.currentData() or "en"
        self._busy = True
        self.send_btn.setEnabled(False)
        self.output.setText(t("ui.compose_trans.translating"))

        thread = QThread(self)
        worker = ComposeTranslateWorker(text=text, target=target)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_thread)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
        self._busy = False
        self.send_btn.setEnabled(True)

    def _on_finished(self, result: ComposeTranslationResult) -> None:
        if result.error:
            self.output.setText(
                f"<span style='color:#e08585;'>{result.error}</span>"
            )
            return
        self.output.setText(result.text or "")

    def _copy_output(self) -> None:
        text = self.output.text()
        if text:
            QApplication.clipboard().setText(text)

    # --- frameless drag -----------------------------------------------------

    def mousePressEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.button() == Qt.LeftButton:
            self._drag_pos = (
                ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)
            ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        self._drag_pos = None
