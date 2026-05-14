"""Render the master icon.svg into PNG / ICO / ICNS bundles.

Run from the repo root::

    python scripts/build_icons.py

Outputs land in ``src/hots_helper/ui/assets/``:

* ``icon-16.png`` … ``icon-512.png`` — sizes Qt picks from at runtime.
* ``icon.ico`` — multi-res Windows app/launcher icon used by PyInstaller.
* ``icon.icns`` — macOS .app icon (only generated when ``iconutil`` exists).

Re-render whenever the SVG changes. The PNGs are committed so end users
on machines without Qt SVG support still see the right icon.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

SIZES = (16, 32, 48, 64, 128, 256, 512)
# Apple expects these exact sizes inside the .iconset bundle.
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def _render_svg(svg_path: Path, size: int) -> Image.Image:
    renderer = QSvgRenderer(str(svg_path))
    img = QImage(QSize(size, size), QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = Path(f.name)
    img.save(str(tmp), "PNG")
    pil = Image.open(tmp).convert("RGBA").copy()
    tmp.unlink(missing_ok=True)
    return pil


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    svg = repo / "src" / "hots_helper" / "ui" / "assets" / "icon.svg"
    out = svg.parent
    if not svg.is_file():
        sys.exit(f"icon.svg missing at {svg}")

    # Need a QApplication for QImage / QPainter on some platforms.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    _ = QApplication.instance() or QApplication([])

    pngs = {s: _render_svg(svg, s) for s in SIZES}
    for s, img in pngs.items():
        path = out / f"icon-{s}.png"
        img.save(path, "PNG", optimize=True)
        print(f"  wrote {path.relative_to(repo)} ({s}×{s})")

    # Windows multi-res .ico — Pillow stuffs every requested size into one file.
    ico_path = out / "icon.ico"
    pngs[256].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)],
    )
    print(f"  wrote {ico_path.relative_to(repo)}")

    # macOS .icns — uses Apple's iconutil if available; harmless to skip on Win/Linux.
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as td:
            iconset = Path(td) / "icon.iconset"
            iconset.mkdir()
            for s in ICNS_SIZES:
                src_size = min(s, max(SIZES))
                src_img = _render_svg(svg, src_size if src_size in pngs else max(SIZES))
                if s != src_size:
                    src_img = src_img.resize((s, s), Image.LANCZOS)
                src_img.save(iconset / f"icon_{s}x{s}.png")
                # Apple also expects @2x variants up to 512.
                if s <= 512:
                    hi = _render_svg(svg, min(s * 2, 1024))
                    hi.save(iconset / f"icon_{s}x{s}@2x.png")
            icns_path = out / "icon.icns"
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
                check=True,
            )
            print(f"  wrote {icns_path.relative_to(repo)}")
    else:
        print("  (skipping .icns — iconutil not on PATH)")


if __name__ == "__main__":
    main()
