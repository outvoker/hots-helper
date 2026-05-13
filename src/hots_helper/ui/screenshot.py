"""Fullscreen screenshot helper. Uses ``mss`` for portability."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import mss
import mss.tools

from ..config import screenshots_dir


def capture_fullscreen() -> Path:
    """Grab all monitors and save a PNG. Returns the saved path."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = screenshots_dir() / f"prematch-{ts}.png"
    with mss.mss() as sct:
        mon = sct.monitors[0]  # virtual screen, spans all displays
        raw = sct.grab(mon)
        mss.tools.to_png(raw.rgb, raw.size, output=str(out))
    return out
