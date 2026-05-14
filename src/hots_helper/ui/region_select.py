"""Modal region selector over a saved screenshot.

When OCR misses or misreads a name, the user can press 🎯 on the slot,
draw a tight rectangle over the actual name on the screenshot, and we
re-run OCR on just that crop. The crop is so small and free of
surrounding UI noise that recognition becomes much more reliable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout


class RegionSelectorDialog(QDialog):
    """Show the screenshot full-screen, let the user drag a selection rect."""

    region_picked = Signal(int, int, int, int)  # x, y, w, h in image pixels

    def __init__(self, screenshot_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select player name region")
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

        self._label = QLabel(self)
        self._label.setPixmap(self._scaled)
        self._label.setGeometry(0, 0, self._scaled.width(), self._scaled.height())

        self._origin: QPoint | None = None
        self._rect: QRect | None = None
        self._hint = QLabel(
            "Drag a tight rectangle over the player name. Esc to cancel.",
            self,
        )
        self._hint.setStyleSheet(
            "background: rgba(0,0,0,180); color: #fc6; padding: 6px 12px; "
            "border-radius: 6px; font-weight: 600;"
        )
        self._hint.adjustSize()
        self._hint.move(20, 20)
        self._hint.raise_()

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

    def paintEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(ev)
        if self._rect is None:
            return
        painter = QPainter(self)
        pen = QPen(Qt.yellow)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(self._rect)


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
