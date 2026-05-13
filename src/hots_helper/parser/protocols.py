"""Load a protocol module for a given replay build.

Works around two issues in the upstream ``heroprotocol`` package:

1. ``heroprotocol.versions`` still uses the removed ``imp`` module on
   Python 3.12+.
2. The PyPI release does not include protocol files newer than ~91756, so
   current CN replays (build 96881+) have no exact match. We fall back to the
   newest protocol that is <= the requested build, searching bundled
   ``heroprotocol`` versions first, then our own ``vendor/`` copy.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType

_PROTOCOL_RE = re.compile(r"protocol(\d+)\.py$")


def _load_py(path: Path, module_name: str) -> ModuleType:
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load protocol from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _collect_protocol_dirs() -> list[Path]:
    dirs: list[Path] = []
    try:
        import heroprotocol.versions as hv

        dirs.append(Path(os.path.dirname(hv.__file__)))
    except Exception:
        pass
    dirs.append(Path(__file__).resolve().parent.parent / "vendor")
    return dirs


def _scan_available() -> dict[int, Path]:
    found: dict[int, Path] = {}
    for d in _collect_protocol_dirs():
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            m = _PROTOCOL_RE.match(entry.name)
            if not m:
                continue
            build = int(m.group(1))
            # First directory in the list wins unless later dir has newer
            # same-build copy; either way exact match would be identical.
            found.setdefault(build, entry)
    return found


def load_protocol_for_build(build: int) -> tuple[ModuleType, int]:
    """Return ``(module, actual_build)``. ``actual_build`` may be <= build."""
    available = _scan_available()
    if not available:
        raise RuntimeError("no protocol files found")
    if build in available:
        chosen = build
    else:
        lower = [b for b in available if b <= build]
        if not lower:
            # requested build is older than anything we have; fall back to the
            # oldest available, which will still decode details/attributes for
            # most stable fields.
            chosen = min(available)
        else:
            chosen = max(lower)
    path = available[chosen]
    module = _load_py(path, f"hots_helper_protocol_{chosen}")
    return module, chosen


def load_latest_protocol() -> ModuleType:
    available = _scan_available()
    latest = max(available)
    return _load_py(available[latest], f"hots_helper_protocol_{latest}")
