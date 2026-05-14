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
    """Import the protocol file as a module.

    If a previous load left a half-initialised module in ``sys.modules``
    (Python's loader stashes one *before* running the body, so an
    exception during ``import six`` or similar leaves an empty shell
    behind), drop that shell and try again — otherwise the next caller
    gets ``module ... has no attribute decode_replay_header``.
    """
    cached = sys.modules.get(module_name)
    if cached is not None and hasattr(cached, "decode_replay_header"):
        return cached
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load protocol from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _collect_protocol_dirs() -> list[Path]:
    """Return the directories scanned for ``protocolXXXXX.py`` files,
    *vendor first*.

    Our vendor copies have been patched to drop the ``six`` /
    ``heroprotocol.decoders`` dependencies, so they import cleanly inside
    the frozen .exe. Upstream ``heroprotocol``'s own protocol files
    *cannot* import in our build (its package ``__init__`` chain reaches
    for the removed ``imp`` module), so they're a fallback only —
    ``_load_py`` will raise on them and the scanner moves on.
    """
    dirs: list[Path] = []
    dirs.append(Path(__file__).resolve().parent.parent / "vendor")
    try:
        import heroprotocol.versions as hv

        dirs.append(Path(os.path.dirname(hv.__file__)))
    except Exception:
        pass
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


def _candidates_for_build(available: dict[int, Path], build: int) -> list[int]:
    """Builds to try for a given replay build, in preference order.

    Exact match first, then the closest-lower, then progressively older
    builds, then anything left (newer-than-requested). Iterating lets us
    skip protocol files that fail to import (e.g. upstream
    ``heroprotocol`` versions that reach for ``six``)."""
    if not available:
        return []
    builds = sorted(available)
    lower = [b for b in builds if b <= build]
    higher = [b for b in builds if b > build]
    # closest-lower first (most likely to decode correctly), then
    # progressively older lower builds, then higher builds as a last resort.
    return list(reversed(lower)) + higher


def load_protocol_for_build(build: int) -> tuple[ModuleType, int]:
    """Return ``(module, actual_build)``. ``actual_build`` may be <= build."""
    available = _scan_available()
    if not available:
        raise RuntimeError("no protocol files found")
    last_err: Exception | None = None
    for chosen in _candidates_for_build(available, build):
        try:
            module = _load_py(
                available[chosen], f"hots_helper_protocol_{chosen}"
            )
            return module, chosen
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(
        f"no usable protocol could be loaded (last error: {last_err})"
    )


def load_latest_protocol() -> ModuleType:
    available = _scan_available()
    if not available:
        raise RuntimeError("no protocol files found")
    last_err: Exception | None = None
    # Try newest first, fall back to older copies if the newest fails.
    for chosen in sorted(available, reverse=True):
        try:
            return _load_py(
                available[chosen], f"hots_helper_protocol_{chosen}"
            )
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(
        f"no usable protocol could be loaded (last error: {last_err})"
    )
