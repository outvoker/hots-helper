# PyInstaller spec for HotS Helper.
#
# Build on the target OS:
#   Windows:  pyinstaller packaging/hots-helper.spec --clean
#   macOS:    pyinstaller packaging/hots-helper.spec --clean
#
# onedir output (not onefile) so the bundled protocol*.py files can be
# loaded by importlib at runtime without extraction overhead.

import sys
from pathlib import Path

block_cipher = None

project_root = Path.cwd()
# Use the standalone launcher (packaging/launcher.py) as the entry
# script. PyInstaller treats the entry as __main__, and __main__ has no
# parent package — so importing the UI directly via app.py would break
# every ``from ..config import …`` it does. The launcher is a plain
# top-level script that calls ``hots_helper.ui.app.main`` after the
# package is registered as an absolute import target.
entry = project_root / "packaging" / "launcher.py"


def _windows_vc_runtime_dlls() -> list[tuple[str, str]]:
    """Return ``(src, dest)`` pairs for the Visual C++ runtime DLLs.

    Python 3.13's ``python313.dll`` depends on ``vcruntime140.dll`` /
    ``vcruntime140_1.dll`` / ``msvcp140.dll`` / ``ucrtbase.dll`` (parts of
    the VC++ 2015-2022 Redistributable). On a developer's box those are
    almost always present (installed by VS, Office, games, drivers…) so
    PyInstaller's build doesn't notice they're missing. On a clean
    Windows machine the redistributable might not be installed, and the
    user gets:

        Failed to load Python DLL '...\\python313.dll'.
        LoadLibrary: 找不到指定的模块.

    To make the bundle self-contained we copy these DLLs into the
    ``_internal`` folder ourselves. The bundle ends up ~3-5 MB larger,
    but recipients don't have to install anything.
    """
    if sys.platform != "win32":
        return []
    import ctypes.util
    import os
    candidates = [
        "vcruntime140.dll",
        "vcruntime140_1.dll",  # 64-bit only — MSVC 2017+
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "concrt140.dll",
        "ucrtbase.dll",
    ]
    search_paths = [
        Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32",
        Path(sys.base_prefix),
        Path(sys.base_prefix) / "DLLs",
    ]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        for d in search_paths:
            p = d / name
            if p.is_file():
                out.append((str(p), "."))
                seen.add(name)
                break
    return out


binaries = _windows_vc_runtime_dlls()

datas = [
    # Bundle the vendored replay protocol files; runtime scans this dir.
    (str(project_root / "src" / "hots_helper" / "vendor"), "hots_helper/vendor"),
    # The SQL schema the DB loads at startup.
    (str(project_root / "src" / "hots_helper" / "db" / "schema.sql"), "hots_helper/db"),
]

# RapidOCR ships its ONNX models + config alongside the package. Without
# this, the frozen app would need users to download models separately.
try:
    from PyInstaller.utils.hooks import collect_data_files
    datas += collect_data_files("rapidocr_onnxruntime")
except Exception:
    pass

# heroprotocol's versions/ dir also needs to be in the bundle so the parser
# can dynamically import whichever protocol matches the replay's build.
try:
    import heroprotocol.versions as _hv
    datas.append((str(Path(_hv.__file__).parent), "heroprotocol/versions"))
except Exception:
    pass

hiddenimports = [
    # Make sure the whole hots_helper package and submodules ship — PyInstaller
    # is conservative about following dynamic imports.
    "hots_helper",
    "hots_helper.config",
    "hots_helper.i18n",
    "hots_helper.lookup",
    "hots_helper.bp",
    "hots_helper.stats",
    "hots_helper.vision",
    "hots_helper.db",
    "hots_helper.db.store",
    "hots_helper.parser",
    "hots_helper.parser.replay",
    "hots_helper.parser.protocols",
    "hots_helper.watcher",
    "hots_helper.watcher.ingest",
    "hots_helper.watcher.watcher",
    "hots_helper.cli",
    "hots_helper.cli.main",
    "hots_helper.ui",
    "hots_helper.ui.app",
    "hots_helper.ui.main_window",
    "hots_helper.ui.popup",
    "hots_helper.ui.aram",
    "hots_helper.ui.region_select",
    "hots_helper.ui.workers",
    "hots_helper.ui.hotkey",
    "hots_helper.ui.screenshot",
    "hots_helper.ui.calibrate",
    "hots_helper.ocr",
    "hots_helper.ocr.rapid",
    "hots_helper.vendor",
    # PySide6 modules referenced only lazily
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    # watchdog picks a platform-specific observer at runtime
    "watchdog.observers.fsevents",
    "watchdog.observers.read_directory_changes",
    "watchdog.observers.inotify",
    "watchdog.observers.kqueue",
    "watchdog.observers.polling",
    # RapidOCR + ONNX runtime (loaded lazily by hots_helper.ocr.rapid)
    "rapidocr_onnxruntime",
    "hots_helper.ocr.rapid",
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "shapely",
    "shapely.geometry",
    "pyclipper",
]

# Platform-specific OCR backends are imported lazily by hots_helper.ocr.
if sys.platform == "win32":
    hiddenimports += [
        "hots_helper.ocr.winrt_ocr",
        "winrt.windows.media.ocr",
        "winrt.windows.graphics.imaging",
        "winrt.windows.storage",
        "winrt.windows.storage.streams",
        "winrt.windows.globalization",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
    ]
elif sys.platform == "darwin":
    hiddenimports += ["hots_helper.ocr.vision_macos"]

a = Analysis(
    [str(entry)],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "PIL.ImageQt"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HotS-Helper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no terminal window on Windows
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HotS-Helper",
)
