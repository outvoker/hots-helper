"""Modal region selector over a saved screenshot.

When OCR misses or misreads a name, the user can press 🎯 on the slot,
draw a tight rectangle over the actual name on the screenshot, and we
re-run OCR on just that crop. The crop is so small and free of
surrounding UI noise that recognition becomes much more reliable.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QGuiApplication,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QDialog, QLabel

from ..i18n import t


class RegionSelectorDialog(QDialog):
    """Show the screenshot fitted to the screen, let the user drag a rect."""

    region_picked = Signal(int, int, int, int)  # x, y, w, h in image pixels

    def __init__(self, screenshot_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("ui.popup.region.title"))
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setModal(True)
        self.setCursor(QCursor(Qt.CrossCursor))

        self._pixmap = QPixmap(str(screenshot_path))
        if self._pixmap.isNull():
            raise RuntimeError(f"could not load {screenshot_path}")

        # Fit to screen with letterbox; keep the original-pixel scale so we
        # can map drag coords back accurately.
        screen = QGuiApplication.primaryScreen().availableGeometry()
        max_w = int(screen.width() * 0.95)
        max_h = int(screen.height() * 0.95)
        self._scaled = self._pixmap.scaled(
            max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._scale_x = self._pixmap.width() / self._scaled.width()
        self._scale_y = self._pixmap.height() / self._scaled.height()

        self.setFixedSize(self._scaled.size())

        # Don't use a QLabel — we paint the pixmap ourselves so the
        # selection rectangle paint happens on the same surface and isn't
        # hidden behind a child widget.
        self._origin: QPoint | None = None
        self._rect: QRect | None = None

        self._hint = QLabel(t("ui.popup.region.hint"), self)
        self._hint.setStyleSheet(
            "background: rgba(0,0,0,180); color: #fc6; padding: 6px 12px; "
            "border-radius: 6px; font-weight: 600;"
        )
        self._hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._hint.adjustSize()
        self._hint.move(20, 20)

    # --- mouse handling -----------------------------------------------------

    def mousePressEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.button() == Qt.LeftButton:
            self._origin = ev.position().toPoint()
            self._rect = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if self._origin is not None:
            self._rect = QRect(self._origin, ev.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.button() == Qt.LeftButton and self._rect is not None:
            r = self._rect
            if r.width() < 6 or r.height() < 6:
                # Treat tiny click as cancel.
                self.reject()
                return
            x = int(r.x() * self._scale_x)
            y = int(r.y() * self._scale_y)
            w = int(r.width() * self._scale_x)
            h = int(r.height() * self._scale_y)
            self.region_picked.emit(x, y, w, h)
            self.accept()

    def keyPressEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.key() == Qt.Key_Escape:
            self.reject()

    # --- painting -----------------------------------------------------------

    def paintEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        # Background: the screenshot itself
        painter.drawPixmap(0, 0, self._scaled)

        if self._rect is not None and self._rect.width() > 0:
            # Dim everything outside the selection so the chosen region
            # stands out clearly.
            painter.fillRect(
                0, 0, self.width(), self._rect.top(),
                QColor(0, 0, 0, 110),
            )
            painter.fillRect(
                0, self._rect.bottom() + 1,
                self.width(), self.height() - self._rect.bottom() - 1,
                QColor(0, 0, 0, 110),
            )
            painter.fillRect(
                0, self._rect.top(),
                self._rect.left(), self._rect.height(),
                QColor(0, 0, 0, 110),
            )
            painter.fillRect(
                self._rect.right() + 1, self._rect.top(),
                self.width() - self._rect.right() - 1, self._rect.height(),
                QColor(0, 0, 0, 110),
            )

            # Bright yellow border around the selection.
            pen = QPen(QColor(255, 220, 0))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(QBrush(Qt.NoBrush))
            painter.drawRect(self._rect)

            # Live size label in the top-left corner of the selection.
            painter.setPen(QColor(255, 220, 0))
            txt = f"{self._rect.width()} × {self._rect.height()}"
            painter.fillRect(
                self._rect.left(), max(0, self._rect.top() - 22),
                10 + 8 * len(txt), 20,
                QColor(0, 0, 0, 200),
            )
            painter.drawText(
                self._rect.left() + 4,
                max(0, self._rect.top() - 22) + 14,
                txt,
            )


def ocr_crop(screenshot_path: Path, x: int, y: int, w: int, h: int) -> str:
    """Crop the screenshot and run OCR on just that region.

    Returns the highest-confidence text the OCR engine produced inside
    the crop, or the empty string when nothing readable was found.
    """
    from ..ocr import recognize
    import tempfile

    with Image.open(screenshot_path) as im:
        crop = im.crop((x, y, x + w, y + h))
        # Padding helps OCR detect the full glyph extents.
        pad = 8
        padded = Image.new("RGB", (crop.width + 2 * pad, crop.height + 2 * pad), (0, 0, 0))
        padded.paste(crop, (pad, pad))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = Path(f.name)
        padded.save(tmp)
    try:
        blocks = recognize(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    if not blocks:
        return ""
    best = max(blocks, key=lambda b: b.confidence)
    return best.text.strip()
