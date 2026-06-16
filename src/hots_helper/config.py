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


# Tags accepted in ``Config.ocr_languages``. Mirrors the keys defined in
# ``hots_helper.ocr.rapid._LANGS``.
_OCR_LANGUAGE_TAGS = ("cn+en", "korean", "japanese")


def _load_ocr_languages(raw) -> list[str]:
    """Sanitise the on-disk ``ocr_languages`` value.

    Old configs won't have the field; brand-new installs default to
    Chinese + Korean. We also drop unknown tags so a stale config
    can't sneak invalid values into the OCR pipeline.
    """
    if raw is None:
        return ["cn+en", "korean"]
    if not isinstance(raw, list):
        return ["cn+en", "korean"]
    out = [str(t) for t in raw if isinstance(t, str) and t in _OCR_LANGUAGE_TAGS]
    # Always keep cn+en — it's the only model that covers English at
    # all, and turning it off would silently break English chat /
    # English handles. Re-add quietly if the user dropped it.
    if "cn+en" not in out:
        out.insert(0, "cn+en")
    return out


@dataclass
class Config:
    recording_roots: list[str] = field(default_factory=list)
    # Global hotkey string in pynput canonical form, e.g. "<ctrl>+<shift>+h".
    hotkey: str = "<ctrl>+<shift>+h"
    # In-game chat translation — OCR the screen, translate every chat
    # line to Chinese. Default: <ctrl>+<shift>+t (mnemonic: translate).
    chat_translate_hotkey: str = "<ctrl>+<shift>+t"
    # Compose-and-translate — open a small input box, user types
    # Chinese, picks target language, gets translation back to copy.
    # Default: <ctrl>+<shift>+y (next to T on QWERTY).
    compose_translate_hotkey: str = "<ctrl>+<shift>+y"
    # Floating always-on-top launcher: visible by default. Position is
    # remembered so the chip stays where the user dragged it. Negative
    # ``-1`` means "we haven't placed it yet — drop into a sensible
    # default corner on next launch".
    launcher_visible: bool = True
    launcher_x: int = -1
    launcher_y: int = -1
    # If True, run the watcher in the background on UI start.
    auto_watch: bool = True
    # If True, kick off a directory scan once the main window has settled.
    # Cheap on subsequent launches because scan_index lets us skip every
    # already-seen file without parsing it.
    auto_scan_on_start: bool = True
    # UI locale, "zh" or "en".
    language: str = "zh"
    # OCR language packs to run. Each tag corresponds to one engine in
    # ``hots_helper.ocr.rapid._LANGS``:
    #   * ``cn+en``    — Chinese + Latin alphabet (covers English on its own).
    #   * ``korean``   — Hangul + Latin.
    #   * ``japanese`` — Hiragana / Katakana / Kanji + Latin.
    # Each enabled language adds ~1s to OCR wall time, so the default is
    # the cheapest combo that still covers the squad's KR servers
    # (CN+EN gives us English handles for free).
    ocr_languages: list[str] = field(
        default_factory=lambda: ["cn+en", "korean"]
    )
    # Cloud sync — empty string means "disabled". Both must be set.
    supabase_url: str = ""
    supabase_anon_key: str = ""
    # Whether to sync automatically on startup + after each ingest.
    sync_auto: bool = True
    # The user's squad roster — toon_handles that count as "us" in the
    # weekly report and get highlighted in the rankings. Empty + not
    # configured means "fall back to the play-frequency heuristic". We
    # no longer assume a fixed five-person squad, so each install picks
    # its own members. ``squad_configured`` distinguishes "deliberately
    # empty" from "never chosen" so the weekly report can prompt once.
    squad_handles: list[str] = field(default_factory=list)
    squad_configured: bool = False

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
            chat_translate_hotkey=str(
                raw.get("chat_translate_hotkey") or "<ctrl>+<shift>+t"
            ),
            compose_translate_hotkey=str(
                raw.get("compose_translate_hotkey") or "<ctrl>+<shift>+y"
            ),
            launcher_visible=bool(raw.get("launcher_visible", True)),
            launcher_x=int(raw.get("launcher_x") or -1),
            launcher_y=int(raw.get("launcher_y") or -1),
            auto_watch=bool(raw.get("auto_watch", True)),
            auto_scan_on_start=bool(raw.get("auto_scan_on_start", True)),
            language=str(raw.get("language") or "zh"),
            ocr_languages=_load_ocr_languages(raw.get("ocr_languages")),
            supabase_url=str(raw.get("supabase_url") or ""),
            supabase_anon_key=str(raw.get("supabase_anon_key") or ""),
            sync_auto=bool(raw.get("sync_auto", True)),
            squad_handles=[
                str(h) for h in (raw.get("squad_handles") or []) if str(h).strip()
            ],
            squad_configured=bool(raw.get("squad_configured", False)),
        )

    @classmethod
    def autodetect(cls) -> "Config":
        roots = [str(p) for p in default_hots_replay_roots() if p.exists()]
        return cls(recording_roots=roots)

    def save(self) -> None:
        config_path().write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8"
        )

    def squad_override(self) -> tuple[str, ...] | None:
        """The configured roster as a tuple, or ``None`` to use the
        heuristic. ``None`` when the user hasn't chosen yet *or* chose an
        empty set — both mean "let the play-frequency heuristic decide"
        rather than "report on nobody"."""
        if not self.squad_configured or not self.squad_handles:
            return None
        return tuple(self.squad_handles)

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
