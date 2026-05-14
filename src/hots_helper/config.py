"""Persistent user config.

Lives in the platform-standard user config dir (``~/.config/hots-helper`` on
Linux, ``~/Library/Application Support/hots-helper`` on macOS,
``%APPDATA%\\hots-helper`` on Windows).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "hots-helper"


def config_dir() -> Path:
    d = Path(user_config_dir(APP_NAME))
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    d = Path(user_data_dir(APP_NAME))
    d.mkdir(parents=True, exist_ok=True)
    return d


def screenshots_dir() -> Path:
    d = data_dir() / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def default_db_path() -> Path:
    return data_dir() / "hots.db"


def default_hots_replay_roots() -> list[Path]:
    """Reasonable guesses for the "Heroes of the Storm" replay root directory.

    The final path under each root is ``<root>/Accounts``. Each Accounts folder
    contains one or more numeric account dirs, which each contain
    region-specific ``<N>-Hero-<R>-<ID>`` dirs, which contain
    ``Replays/Multiplayer/*.StormReplay``. We return plausible roots; the
    caller walks down.
    """
    candidates: list[Path] = []
    if sys.platform == "win32":
        # OneDrive-redirected Documents and plain Documents both show up.
        user_profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        for docs in {
            user_profile / "Documents",
            user_profile / "OneDrive" / "Documents",
            user_profile / "OneDrive" / "文档",
        }:
            candidates.append(docs / "Heroes of the Storm")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "Blizzard" / "Heroes of the Storm")
        candidates.append(Path.home() / "Documents" / "Heroes of the Storm")
    else:
        candidates.append(Path.home() / "Documents" / "Heroes of the Storm")
    return candidates


def discover_replay_dirs(root: Path) -> list[Path]:
    """Given a HotS replay root, find every ``Replays/Multiplayer`` directory.

    Tolerates the real layout: ``root/Accounts/<num>/<region>-Hero-<r>-<id>/Replays/Multiplayer``.
    """
    if not root.exists():
        return []
    accounts = root / "Accounts"
    if not accounts.is_dir():
        # Maybe the user pointed directly at the Accounts dir or a specific
        # player dir; try both.
        if (root / "Replays" / "Multiplayer").is_dir():
            return [root / "Replays" / "Multiplayer"]
        accounts = root
    out: list[Path] = []
    for account in sorted(accounts.iterdir()):
        if not account.is_dir():
            continue
        for player in sorted(account.iterdir()):
            if not player.is_dir():
                continue
            mp = player / "Replays" / "Multiplayer"
            if mp.is_dir():
                out.append(mp)
    return out


@dataclass
class Config:
    recording_roots: list[str] = field(default_factory=list)
    # Global hotkey string in pynput canonical form, e.g. "<ctrl>+<shift>+h".
    hotkey: str = "<ctrl>+<shift>+h"
    # If True, run the watcher in the background on UI start.
    auto_watch: bool = True
    # UI locale, "zh" or "en".
    language: str = "zh"
    # Cloud sync — empty string means "disabled". Both must be set.
    supabase_url: str = ""
    supabase_anon_key: str = ""
    # Whether to sync automatically on startup + after each ingest.
    sync_auto: bool = True

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            return cls.autodetect()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except Exception:
            return cls.autodetect()
        return cls(
            recording_roots=list(raw.get("recording_roots") or []),
            hotkey=str(raw.get("hotkey") or "<ctrl>+<shift>+h"),
            auto_watch=bool(raw.get("auto_watch", True)),
            language=str(raw.get("language") or "zh"),
            supabase_url=str(raw.get("supabase_url") or ""),
            supabase_anon_key=str(raw.get("supabase_anon_key") or ""),
            sync_auto=bool(raw.get("sync_auto", True)),
        )

    @classmethod
    def autodetect(cls) -> "Config":
        roots = [str(p) for p in default_hots_replay_roots() if p.exists()]
        return cls(recording_roots=roots)

    def save(self) -> None:
        config_path().write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8"
        )

    def effective_replay_dirs(self) -> list[Path]:
        """Expand every configured root into actual Replays/Multiplayer dirs."""
        out: list[Path] = []
        for r in self.recording_roots:
            p = Path(r).expanduser()
            if not p.exists():
                continue
            # If the root itself is a Multiplayer folder, take it; otherwise
            # walk Accounts/.../Replays/Multiplayer.
            if p.name == "Multiplayer" and p.is_dir():
                out.append(p)
                continue
            found = discover_replay_dirs(p)
            if found:
                out.extend(found)
            elif p.is_dir():
                out.append(p)
        # Dedupe while preserving order.
        seen: set[Path] = set()
        uniq: list[Path] = []
        for d in out:
            d = d.resolve()
            if d not in seen:
                seen.add(d)
                uniq.append(d)
        return uniq
