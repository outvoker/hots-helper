"""Interactive calibration tool.

Given a sample BP-phase screenshot at the user's native resolution, have them
click the name tab + map title. Save normalized (0..1) coordinates into
the config file so future screenshots at the same aspect ratio are parsed
automatically.

Usage:
    hots-calibrate path/to/screenshot.png
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QGuiApplication, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from ..config import config_dir


CALIBRATION_LABELS = [
    "Map title",
    "Ally slot 1", "Ally slot 2", "Ally slot 3", "Ally slot 4", "Ally slot 5",
    "Enemy slot 1", "Enemy slot 2", "Enemy slot 3", "Enemy slot 4", "Enemy slot 5",
]


@dataclass
class CalibratedLayout:
    # Each entry is a normalized bbox (x0, y0, x1, y1).
    map_title: tuple[float, float, float, float]
    ally_names: list[tuple[float, float, float, float]]
    enemy_names: list[tuple[float, float, float, float]]
    # Aspect ratio we calibrated against; used as a sanity check.
    aspect_ratio: float


def _save(layout: CalibratedLayout) -> Path:
    path = config_dir() / "layout.json"
    path.write_text(json.dumps(asdict(layout), indent=2, ensure_ascii=False), "utf-8")
    return path


class _ImageLabel(QLabel):
    """QLabel that displays an image and reports click positions in image pixels."""

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__()
        self._pixmap = pixmap
        self._display_scale = 1.0
        self._points: list[tuple[int, int]] = []
        self._current_box: tuple[int, int, int, int] | None = None
        self._drag_start: QPoint | None = None
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setCursor(Qt.CrossCursor)
        self._refresh_pixmap()

    def sizeHint(self):
        return self._pixmap.size() / self._display_scale

    def set_display_scale(self, scale: float) -> None:
        self._display_scale = scale
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        w = int(self._pixmap.width() / self._display_scale)
        h = int(self._pixmap.height() / self._display_scale)
        self.setPixmap(self._pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _to_image_coord(self, p: QPoint) -> tuple[int, int]:
        return (int(p.x() * self._display_scale), int(p.y() * self._display_scale))

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() == Qt.LeftButton:
            self._drag_start = ev.position().toPoint()

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:
        if ev.button() == Qt.LeftButton and self._drag_start is not None:
            p0 = self._to_image_coord(self._drag_start)
            p1 = self._to_image_coord(ev.position().toPoint())
            x0, x1 = sorted((p0[0], p1[0]))
            y0, y1 = sorted((p0[1], p1[1]))
            self._current_box = (x0, y0, x1, y1)
            self._drag_start = None
            self.window().on_box_drawn(self._current_box)


class CalibrateWindow(QMainWindow):
    def __init__(self, image_path: Path) -> None:
        super().__init__()
        self.setWindowTitle("HotS Helper — Calibrate layout")
        self.image_path = image_path
        self._pixmap = QPixmap(str(image_path))
        if self._pixmap.isNull():
            raise RuntimeError(f"could not load image: {image_path}")
        self._boxes: list[tuple[int, int, int, int]] = []
        self._idx = 0

        screen_size = QGuiApplication.primaryScreen().availableSize()
        scale = max(1.0,
                    self._pixmap.width() / (screen_size.width() * 0.9),
                    self._pixmap.height() / (screen_size.height() * 0.85))

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        self.hint = QLabel()
        self.hint.setStyleSheet("font-size: 14pt; font-weight: 600; color: #fdd; padding: 6px;")
        v.addWidget(self.hint)

        self.image_label = _ImageLabel(self._pixmap)
        self.image_label.set_display_scale(scale)
        v.addWidget(self.image_label)

        buttons = QVBoxLayout()
        self.confirm_btn = QPushButton("Use this box, next →")
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.clicked.connect(self._next)
        buttons.addWidget(self.confirm_btn)
        self.redo_btn = QPushButton("Redo this box")
        self.redo_btn.setEnabled(False)
        self.redo_btn.clicked.connect(self._redo)
        buttons.addWidget(self.redo_btn)
        v.addLayout(buttons)

        self._update_hint()

    def _update_hint(self) -> None:
        if self._idx >= len(CALIBRATION_LABELS):
            self.hint.setText("Done. Click save to write layout.json.")
        else:
            label = CALIBRATION_LABELS[self._idx]
            self.hint.setText(
                f"Drag a tight rectangle around: <b>{label}</b>  "
                f"({self._idx + 1} / {len(CALIBRATION_LABELS)})"
            )

    def on_box_drawn(self, box: tuple[int, int, int, int]) -> None:
        self._boxes.append(box) if len(self._boxes) == self._idx else self._boxes.__setitem__(self._idx, box)
        self.confirm_btn.setEnabled(True)
        self.redo_btn.setEnabled(True)

    def _next(self) -> None:
        self._idx += 1
        self.confirm_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        if self._idx >= len(CALIBRATION_LABELS):
            self._save_and_exit()
            return
        self._update_hint()

    def _redo(self) -> None:
        if self._boxes and len(self._boxes) > self._idx:
            self._boxes = self._boxes[: self._idx]
        self.confirm_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)

    def _save_and_exit(self) -> None:
        w, h = self._pixmap.width(), self._pixmap.height()

        def norm(b):
            x0, y0, x1, y1 = b
            return (x0 / w, y0 / h, x1 / w, y1 / h)

        layout = CalibratedLayout(
            map_title=norm(self._boxes[0]),
            ally_names=[norm(b) for b in self._boxes[1:6]],
            enemy_names=[norm(b) for b in self._boxes[6:11]],
            aspect_ratio=w / h,
        )
        path = _save(layout)
        self.hint.setText(f"Saved to {path}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: hots-calibrate <screenshot.png>")
        return 2
    img_path = Path(sys.argv[1])
    if not img_path.exists():
        print(f"not found: {img_path}")
        return 1
    app = QApplication(sys.argv)
    w = CalibrateWindow(img_path)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
