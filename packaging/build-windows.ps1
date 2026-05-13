# Build a Windows onedir bundle for HotS Helper.
# Run this on a Windows machine inside the project directory:
#
#   PowerShell:
#     uv sync
#     .\packaging\build-windows.ps1
#
# Output appears under dist\HotS-Helper\. Ship the whole folder (or zip it);
# end users double-click HotS-Helper.exe — no Python install required.

$ErrorActionPreference = "Stop"

Write-Host "Ensuring dev deps…"
uv sync --group dev

Write-Host "Running PyInstaller…"
uv run pyinstaller packaging\hots-helper.spec --clean --noconfirm

Write-Host ""
Write-Host "Build done: dist\HotS-Helper\HotS-Helper.exe"
