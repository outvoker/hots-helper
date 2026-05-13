"""Ban/pick-oriented player lookup.

Given ten in-game names + a map, return per-player structured data that's
useful while drafting: signature heroes, map-specific performance, teammates,
recent form, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Store
from .stats import wilson_lower_bound


# --- dataclasses --------------------------------------------------------------


@dataclass
class HeroUsage:
    hero: str
    hero_id: str
    games: int
    wins: int
    avg_k: float
    avg_d: float
    avg_a: float
    avg_hero_dmg: float
    avg_siege_dmg: float
    avg_structure_dmg: float
    avg_healing: float
    avg_xp: float
    avg_cc: float
    avg_streak: float
    last_played: str
    # Most-picked talent by tier, as (tier, talent, pick_rate, winrate). Only
    # filled when the caller asks for a single player's hero breakdown.
    top_talents: list[tuple[int, str, float, float]] = field(default_factory=list)

    @property
    def winrate(self) -> float:
        return (self.wins / self.games) if self.games else 0.0


@dataclass
class RecentMatch:
    played_at: str
    map_name: str
    mode: str
    hero: str
    hero_id: str
    result: int
    kills: int
    deaths: int
    assists: int
    hero_damage: int
    siege_damage: int
    healing: int
    bans_team0: str
    bans_team1: str


@dataclass
class MapRecord:
    map_name: str
    games: int
    wins: int

    @property
    def winrate(self) -> float:
        return (self.wins / self.games) if self.games else 0.0


@dataclass
class TeammateEntry:
    display_name: str
    toon_handle: str
    games: int
    shared_wins: int

    @property
    def shared_winrate(self) -> float:
        return (self.shared_wins / self.games) if self.games else 0.0


@dataclass
class PlayerSummary:
    name_searched: str
    toon_handle: str
    display_name: str
    total_games: int
    total_wins: int
    overall_kda: tuple[float, float, float]
    recent_games: int       # games in the last 30 days
    recent_wins: int
    map_games: int          # games on the focused map, if any
    map_wins: int
    signature_heroes: list[HeroUsage] = field(default_factory=list)
    map_heroes: list[HeroUsage] = field(default_factory=list)
    frequent_teammates: list[TeammateEntry] = field(default_factory=list)
    frequent_opponents: list[TeammateEntry] = field(default_factory=list)
    recent_matches: list[RecentMatch] = field(default_factory=list)
    map_records: list[MapRecord] = field(default_factory=list)
    ban_recommendations: list[HeroUsage] = field(default_factory=list)
    note: str = ""

    @property
    def winrate(self) -> float:
        return (self.total_wins / self.total_games) if self.total_games else 0.0

    @property
    def map_winrate(self) -> float:
        return (self.map_wins / self.map_games) if self.map_games else 0.0

    @property
    def recent_winrate(self) -> float:
        return (self.recent_wins / self.recent_games) if self.recent_games else 0.0


# --- internals ----------------------------------------------------------------


def _hero_usage_from_row(row: Any) -> HeroUsage:
    return HeroUsage(
        hero=row["hero"],
        hero_id=row["hero_id"] or "",
        games=int(row["games"]),
        wins=int(row["wins"] or 0),
        avg_k=float(row["avg_k"] or 0.0),
        avg_d=float(row["avg_d"] or 0.0),
        avg_a=float(row["avg_a"] or 0.0),
        avg_hero_dmg=float(row["avg_hero_dmg"] or 0.0),
        avg_siege_dmg=float(row["avg_siege_dmg"] or 0.0),
        avg_structure_dmg=float(row["avg_structure_dmg"] or 0.0),
        avg_healing=float(row["avg_healing"] or 0.0),
        avg_xp=float(row["avg_xp"] or 0.0),
        avg_cc=float(row["avg_cc"] or 0.0),
        avg_streak=float(row["avg_streak"] or 0.0),
        last_played=row["last_played"],
    )


def _summarize(
    store: Store,
    name: str,
    map_name: str | None,
    top_n_heroes: int,
    recent_n: int,
    min_games_for_signature: int,
) -> list[PlayerSummary]:
    rows = store.find_players_by_name(name)
    if not rows:
        return [
            PlayerSummary(
                name_searched=name,
                toon_handle="",
                display_name=name,
                total_games=0, total_wins=0,
                overall_kda=(0.0, 0.0, 0.0),
                recent_games=0, recent_wins=0,
                map_games=0, map_wins=0,
                note="not found in local database",
            )
        ]

    thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    out: list[PlayerSummary] = []

    for row in rows:
        handle = row["toon_handle"]

        overall = store.overall_for_handle(handle)
        games = int(overall["games"] or 0) if overall else 0
        wins = int(overall["wins"] or 0) if overall else 0
        kda = (
            float(overall["avg_k"] or 0.0),
            float(overall["avg_d"] or 0.0),
            float(overall["avg_a"] or 0.0),
        ) if overall else (0.0, 0.0, 0.0)

        recent = store.overall_for_handle(handle, since_iso=thirty_days_ago)
        recent_g = int(recent["games"] or 0) if recent else 0
        recent_w = int(recent["wins"] or 0) if recent else 0

        map_row = store.overall_for_handle(handle, map_name=map_name) if map_name else None
        map_g = int(map_row["games"] or 0) if map_row else 0
        map_w = int(map_row["wins"] or 0) if map_row else 0

        # All-map hero stats -> signature + ban recommendations.
        all_heroes = [_hero_usage_from_row(r) for r in store.hero_stats_for_handle(handle)]

        # Signature: keep the full list ordered by games desc. UI decides how
        # many to show; callers that need a truncated view can slice
        # themselves.
        signature = all_heroes

        # Ban recommendations: must have played the hero enough times for us
        # to trust the signal (min_games_for_signature), AND the Wilson lower
        # bound of their winrate must be >= 0.50. Without the Wilson guard,
        # a lucky 5/5 outranks an earned 14/20.
        candidates = [
            h for h in all_heroes
            if h.games >= min_games_for_signature
            and wilson_lower_bound(h.wins, h.games) >= 0.50
        ]
        candidates.sort(
            key=lambda h: (-wilson_lower_bound(h.wins, h.games), -h.games)
        )
        bans = candidates[:3]
        if not bans:
            # Relax the Wilson threshold but keep the game-count floor.
            relaxed = [h for h in all_heroes if h.games >= min_games_for_signature]
            relaxed.sort(key=lambda h: (-wilson_lower_bound(h.wins, h.games), -h.games))
            bans = relaxed[:3]

        # Map-specific hero usage.
        map_heroes: list[HeroUsage] = []
        if map_name:
            map_heroes = [
                _hero_usage_from_row(r)
                for r in store.hero_stats_for_handle(handle, map_name=map_name)
            ][:top_n_heroes]

        # Teammates / opponents (sample of frequent ones).
        teammates = [
            TeammateEntry(
                display_name=r["display_name"],
                toon_handle=r["toon_handle"],
                games=int(r["games"]),
                shared_wins=int(r["shared_wins"] or 0),
            )
            for r in store.played_with_for_handle(handle, teammate=True, limit=5)
        ]
        opponents = [
            TeammateEntry(
                display_name=r["display_name"],
                toon_handle=r["toon_handle"],
                games=int(r["games"]),
                shared_wins=int(r["shared_wins"] or 0),
            )
            for r in store.played_with_for_handle(handle, teammate=False, limit=5)
        ]

        map_records = [
            MapRecord(
                map_name=r["map_name"],
                games=int(r["games"]),
                wins=int(r["wins"] or 0),
            )
            for r in store.map_winrate_for_handle(handle)
        ]

        recents = [
            RecentMatch(
                played_at=r["played_at"],
                map_name=r["map_name"],
                mode=r["mode"],
                hero=r["hero"],
                hero_id=r["hero_id"] or "",
                result=int(r["result"]),
                kills=int(r["kills"]),
                deaths=int(r["deaths"]),
                assists=int(r["assists"]),
                hero_damage=int(r["hero_damage"]),
                siege_damage=int(r["siege_damage"]),
                healing=int(r["healing"]),
                bans_team0=r["bans_team0"] or "",
                bans_team1=r["bans_team1"] or "",
            )
            for r in store.matches_for_handle(handle, limit=recent_n)
        ]

        out.append(
            PlayerSummary(
                name_searched=name,
                toon_handle=handle,
                display_name=row["display_name"],
                total_games=games,
                total_wins=wins,
                overall_kda=kda,
                recent_games=recent_g,
                recent_wins=recent_w,
                map_games=map_g,
                map_wins=map_w,
                signature_heroes=signature,
                map_heroes=map_heroes,
                frequent_teammates=teammates,
                frequent_opponents=opponents,
                recent_matches=recents,
                map_records=map_records,
                ban_recommendations=bans,
            )
        )
    return out


@dataclass
class HeroReport:
    hero: str
    total_games: int
    total_wins: int
    map_games: int
    map_wins: int
    map_records: list[MapRecord] = field(default_factory=list)
    # per tier: [(talent, games, wins, pick_rate)]
    talents_by_tier: dict[int, list[tuple[str, int, int, float]]] = field(default_factory=dict)

    @property
    def winrate(self) -> float:
        return (self.total_wins / self.total_games) if self.total_games else 0.0

    @property
    def map_winrate(self) -> float:
        return (self.map_wins / self.map_games) if self.map_games else 0.0


def hero_report(store: Store, hero: str, *, map_name: str | None = None) -> HeroReport | None:
    """Hero-centric aggregation; used by CLI ``hero`` and future popup view."""
    overall = store.hero_overall(hero)
    if not overall or not int(overall["games"] or 0):
        return None

    maps = [
        MapRecord(map_name=r["map_name"], games=int(r["games"]), wins=int(r["wins"] or 0))
        for r in store.hero_by_map(hero)
    ]
    map_games = map_wins = 0
    if map_name:
        for m in maps:
            if m.map_name == map_name:
                map_games = m.games
                map_wins = m.wins
                break

    talents = store.hero_talents(hero, map_name=map_name)
    total_by_tier: dict[int, int] = {}
    for r in talents:
        total_by_tier[r["tier"]] = total_by_tier.get(r["tier"], 0) + int(r["games"])
    grouped: dict[int, list[tuple[str, int, int, float]]] = {}
    for r in talents:
        tier = r["tier"]
        games = int(r["games"])
        wins = int(r["wins"])
        pick_rate = games / total_by_tier[tier] if total_by_tier.get(tier) else 0.0
        grouped.setdefault(tier, []).append((r["talent"], games, wins, pick_rate))

    return HeroReport(
        hero=hero,
        total_games=int(overall["games"]),
        total_wins=int(overall["wins"] or 0),
        map_games=map_games,
        map_wins=map_wins,
        map_records=maps,
        talents_by_tier=grouped,
    )


def lookup_players(
    store: Store,
    names: list[str],
    *,
    map_name: str | None = None,
    top_n_heroes: int = 5,
    recent_n: int = 5,
    min_games_for_signature: int = 5,
) -> dict[str, list[PlayerSummary]]:
    """Main entry point. Pass in the pre-game map name if you know it."""
    return {
        n: _summarize(store, n, map_name, top_n_heroes, recent_n, min_games_for_signature)
        for n in names
    }
