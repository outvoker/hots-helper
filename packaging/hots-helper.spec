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
entry = project_root / "src" / "hots_helper" / "ui" / "app.py"

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
    binaries=[],
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
