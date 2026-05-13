# HotS Helper

Local replay analyzer + pre-game scout for Heroes of the Storm.

## What it does

1. **Watches** your replay folder, parses every `.StormReplay` (real talent
   names, K/D/A, damage, healing, awards, bans, …) and stores it in a local
   SQLite DB. Same-match deduplication so teammates' replay copies don't
   inflate stats.
2. Pre-game **hotkey** captures a fullscreen screenshot of the BP screen, runs
   the system OCR (macOS Vision / Windows Media OCR), and pops up a
   floating, always-on-top window with:
   - 🚫 Ban suggestions from each enemy's signature heroes (statistically
     significant lift over their own baseline).
   - ✅ Pick suggestions for the current map (Wilson lower bound + z-test).
   - For each of the 10 players: K/D/A, win-rate, top-3 used heroes, and an
     expand button to see every hero they've played.
3. Names are editable: if OCR mis-reads a name (low-confidence slots are
   highlighted yellow), correct it and press Enter to re-query.

Storm League data only by default. ARAM/Custom replays are still ingested for
your own match history but excluded from BP analysis.

---

## Quick start (development)

Both macOS and Windows:

```bash
# Install uv (https://docs.astral.sh/uv/)

# Clone, sync deps
uv sync

# Run the desktop app
uv run hots-ui

# Or use the CLI
uv run hots scan        # scan the configured/auto-detected replay folder
uv run hots stats       # DB summary
uv run hots bp 巨龙镇 -e Player1 -e Player2 -e Player3 -e Player4 -e Player5
uv run hots hero 阿兹莫丹 --map 巨龙镇
```

The first launch auto-detects the standard HotS replay folder and offers it
in the UI's "Replay folders" section. Click **Start scan** once to ingest
everything; check **Watch for new replays** to keep it live.

---

## Windows

### Path 1: Run from source

1. Install Python 3.11+ from python.org (any modern Python 3.11/3.12/3.13
   works).
2. Install `uv`:
   ```powershell
   winget install --id=astral-sh.uv
   ```
3. From the project folder:
   ```powershell
   uv sync
   uv run hots-ui
   ```
4. **Install the language packs for any names you expect to see**. Windows
   OCR is single-language per pass, so the app fans out across whichever
   languages are installed and merges results. Add as many as you need:
   - Settings → Time & Language → Language & region → Add a language
   - Recommended for an Asian server: 中文（简体, 中国）, 日本語, 한국어
   - For each added language: click it → Language options → ensure
     "Basic typing" (which includes the OCR data) is installed.
   - English-only works if all your matches are alphanumeric names.

### Path 2: Distribute as `.exe` (no Python needed for end users)

Run on a Windows machine (the spec is fine, the bootloader has to be built
on Windows itself):

```powershell
uv sync
uv run pyinstaller packaging\hots-helper.spec --clean --noconfirm
```

Output: `dist\HotS-Helper\HotS-Helper.exe` plus the supporting `_internal`
folder. Zip the whole `HotS-Helper` directory and ship it. Users
double-click the `.exe`; no Python install required.

`packaging/build-windows.ps1` is a thin wrapper that runs the two commands
above for you.

### Default replay folder on Windows

The app auto-detects:

```
%USERPROFILE%\Documents\Heroes of the Storm\Accounts\<id>\<region>-Hero-…\Replays\Multiplayer\
```

OneDrive-redirected Documents (`%USERPROFILE%\OneDrive\Documents\…` and the
Chinese `OneDrive\文档\…`) are also probed. If your install lives elsewhere,
add it manually in the **Replay folders** section.

### Hotkey notes

The default hotkey is `Ctrl + Shift + H`. It uses `pynput` to listen
globally. On Windows that should Just Work — no admin permission required
unless your antivirus is paranoid about input listeners. If your hotkey
collides with another app, change it in the **Pre-game scout hotkey**
section and click Apply.

### Anti-virus / SmartScreen

PyInstaller-built executables sometimes trigger SmartScreen on first run
because the binary isn't code-signed. Click "More info" → "Run anyway",
or sign the binary if you're distributing widely.

---

## macOS

The same `uv run hots-ui` works. The first time you press the hotkey, macOS
will prompt for **Accessibility** and **Screen Recording** permissions:

- System Settings → Privacy & Security → Accessibility → enable Terminal (or
  Python.app) so `pynput` can read global key events.
- System Settings → Privacy & Security → Screen Recording → enable the same
  process so `mss` can capture the full screen.

After granting permissions, restart the app.

OCR uses the built-in Vision framework (macOS 10.15+). No additional setup.

---

## Data layout

| Path | Contents |
|---|---|
| `~/.config/hots-helper/config.json` (Linux) / `~/Library/Application Support/hots-helper/config.json` (mac) / `%APPDATA%\hots-helper\config.json` (Win) | folder list, hotkey, settings |
| `~/Library/Application Support/hots-helper/hots.db` (mac) / `%LOCALAPPDATA%\hots-helper\hots.db` (Win) | the SQLite database |
| `…\hots-helper\screenshots\` | hotkey screenshots, prematch-{ts}.png |

Delete the `.db` file to start fresh. Scanning is idempotent: re-running
`hots scan` only adds new replays; same-match perspectives from teammates
are de-duplicated automatically.

---

## CLI cheat-sheet

```bash
hots scan [folder]         # one-shot ingest (idempotent)
hots watch [folder]        # bootstrap scan + live watcher
hots stats                 # DB summary
hots players               # everybody seen in your replays
hots lookup <name> [--map] # full per-player breakdown
hots hero <hero> [--map]   # hero deep-dive: maps + talents
hots heroes --min N        # all heroes with ≥ N games
hots map <map> --min N     # heroes statistically strong/weak on a map
hots bp <map> -e p1 ... -e p5  # full BP advisor (bans + picks)
```

All commands accept `--db <path>` to override the database location.
