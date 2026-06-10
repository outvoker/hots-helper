"""Weekly squad report — totals, MVPs, highlights, hero pool, map breakdown.

Pure data layer: takes a :class:`hots_helper.db.Store` plus a window
(default 7-day rolling) and returns a :class:`WeeklyReport` dataclass.
The dialog and the clipboard renderer both consume this; nothing in
here imports Qt.

Scope: Storm League only (matches the rest of the app's leaderboards
and BP recommendations). Squad membership is detected via the same
``Store.squad_handles()`` heuristic — top of the play-frequency
distribution.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Store
from .hero_roles import FRONTLINE_ROLES, HEAL_ROLES, hero_role
from .i18n import t

# Role groups the awards scope to. Healing awards → healer+support;
# frontline (tank/soak) → tank+bruiser; damage awards → assassin+bruiser.
_HEAL_ROLES = HEAL_ROLES
_FRONTLINE_ROLES = FRONTLINE_ROLES
_DAMAGE_ROLES: frozenset[str] = frozenset({"assassin", "bruiser"})

# We treat values above this as uint32 sentinel garbage from older replay
# parsers — same threshold the leaderboards use to drop overflow rows.
_METRIC_SANITY_MAX = 10_000_000

# Role-contribution thresholds: a healer's "average healing" should ignore
# the games where they were a tank, and vice versa. Same numbers used by
# player_rank.py so the report is consistent with the leaderboards.
_HEAL_THRESHOLD = 1000
_TAKEN_THRESHOLD = 30000

# Min sample sizes for derived stats — without these a single 100% game
# would top "best winrate", which is not what people read a weekly
# report for.
_MIN_GAMES_HERO_TOP_WR = 3
_MIN_GAMES_PLAYER_HERO_PICK = 1

# A hero pairing is rarer than a single-hero pick (it needs two squad
# members on specific heroes in the *same* game). Combos are computed over
# all history, so we can ask for a real sample before trusting a winrate —
# a 1-2 game pairing's 100% is noise, not a synergy worth reporting.
_MIN_GAMES_HERO_COMBO = 4
# How many top combos to surface.
_MAX_HERO_COMBOS = 5

# Cap how many awards a single squad member can hold. Without this, the
# squad's highest-volume player (e.g. someone with 38 Azmodan games) sweeps
# every cumulative-stat award. Once a player hits the cap, that award rolls
# down to the next-best contestant so the trophies spread across the squad.
_MAX_AWARDS_PER_PLAYER = 3


# --- dataclasses -------------------------------------------------------------


@dataclass
class WindowSummary:
    days: int
    start_iso: str
    end_iso: str
    games: int
    wins: int

    @property
    def winrate(self) -> float:
        return (self.wins / self.games) if self.games else 0.0


@dataclass
class Overview:
    current: WindowSummary
    previous: WindowSummary

    @property
    def games_delta(self) -> int:
        return self.current.games - self.previous.games

    @property
    def winrate_delta_pp(self) -> float:
        """Difference in winrate, in percentage points (current minus previous)."""
        return (self.current.winrate - self.previous.winrate) * 100.0


@dataclass
class PlayerWeekStats:
    toon_handle: str
    display_name: str
    games: int
    wins: int
    avg_k: float
    avg_d: float
    avg_a: float
    most_played_hero: str
    most_played_hero_games: int
    most_played_hero_wins: int

    @property
    def winrate(self) -> float:
        return (self.wins / self.games) if self.games else 0.0


@dataclass
class HeroPickStat:
    """Per-hero usage across the squad in the window."""
    hero: str
    games: int
    wins: int

    @property
    def winrate(self) -> float:
        return (self.wins / self.games) if self.games else 0.0


@dataclass
class HeroComboStat:
    """Win record for a pair of heroes the squad fielded *together* in the
    same game. ``hero_a`` / ``hero_b`` are stored sorted so the same
    pairing always keys identically regardless of pick order."""
    hero_a: str
    hero_b: str
    games: int
    wins: int

    @property
    def winrate(self) -> float:
        return (self.wins / self.games) if self.games else 0.0


@dataclass
class MapStat:
    map_name: str
    games: int
    wins: int

    @property
    def winrate(self) -> float:
        return (self.wins / self.games) if self.games else 0.0


@dataclass
class MvpAward:
    """One MVP-style award (e.g. 战神 / 输出王)."""
    label_key: str       # i18n key for the award label
    display_name: str    # winner's display name
    hero: str            # representative hero (their most-played in window)
    value: float         # the metric we ranked on
    games: int           # how many qualifying games went into the average


@dataclass
class HighlightMatch:
    """One stand-out game in the window."""
    played_at: str
    display_name: str
    hero: str
    map_name: str
    result: int
    kills: int
    deaths: int
    assists: int
    hero_damage: int


@dataclass
class StreakRun:
    """A consecutive run of wins or losses for the squad."""
    length: int
    started_at: str
    ended_at: str

    @property
    def is_empty(self) -> bool:
        return self.length <= 0


@dataclass
class WeeklyReport:
    overview: Overview
    players: list[PlayerWeekStats] = field(default_factory=list)
    awards: list[MvpAward] = field(default_factory=list)
    highlights: list[HighlightMatch] = field(default_factory=list)
    hero_top_picked: list[HeroPickStat] = field(default_factory=list)
    hero_top_winrate: list[HeroPickStat] = field(default_factory=list)
    hero_combos: list[HeroComboStat] = field(default_factory=list)
    maps: list[MapStat] = field(default_factory=list)
    longest_win_streak: StreakRun = field(default_factory=lambda: StreakRun(0, "", ""))
    longest_loss_streak: StreakRun = field(default_factory=lambda: StreakRun(0, "", ""))


# --- helpers -----------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _sanitised(value: Any) -> int:
    """Drop uint32 garbage. Returns 0 when the row's value is unusable."""
    n = int(value or 0)
    if n < 0 or n >= _METRIC_SANITY_MAX:
        return 0
    return n


def _fetch_squad_matches(
    store: Store,
    squad: tuple[str, ...],
    *,
    start_iso: str,
    end_iso: str,
) -> list[Any]:
    """All Storm League player_match rows for our squad in the window.

    One row per (replay × squad member who played it). Joined with the
    replay so we get map_name + played_at without a second query.
    """
    if not squad:
        return []
    placeholders = ",".join("?" for _ in squad)
    sql = f"""
        SELECT pm.toon_handle, pm.display_name AS pm_display_name,
               pm.hero, pm.hero_id, pm.team, pm.result,
               pm.kills, pm.deaths, pm.assists,
               pm.takedowns, pm.solo_kills,
               pm.hero_damage, pm.siege_damage, pm.structure_damage,
               pm.healing, pm.damage_taken, pm.damage_soaked,
               pm.experience_contribution AS xp,
               pm.time_cc_enemy_heroes    AS cc,
               pm.time_stunning_enemy_heroes AS stun,
               pm.on_fire_time            AS on_fire,
               pm.time_spent_dead         AS dead,
               pm.outnumbered_deaths      AS outnumbered,
               pm.merc_camp_captures      AS mercs,
               pm.watch_tower_captures    AS towers,
               pm.teamfight_hero_damage   AS tf_dmg,
               pm.clutch_heals            AS clutch,
               pm.protection_given_to_allies AS protection,
               pm.escapes_performed       AS escapes,
               r.id AS replay_id, r.played_at, r.map_name, r.duration_s
        FROM player_match pm
        JOIN replays r ON r.id = pm.replay_id
        WHERE pm.toon_handle IN ({placeholders})
          AND r.mode = 'Storm League'
          AND r.played_at >= ?
          AND r.played_at <  ?
        ORDER BY r.played_at ASC
    """
    return store.conn.execute(
        sql, (*squad, start_iso, end_iso)
    ).fetchall()


def _fetch_squad_combo_rows(store: Store, squad: tuple[str, ...]) -> list[Any]:
    """All-time Storm League (replay_id, hero, result) rows for the squad.

    Hero combos are computed over the *entire history*, not the rolling
    window, so a pairing's winrate reflects every game the squad has ever
    fielded it. Only the three columns the pairing math needs are
    selected — no window bound.
    """
    if not squad:
        return []
    placeholders = ",".join("?" for _ in squad)
    sql = f"""
        SELECT pm.hero, pm.result, r.id AS replay_id
        FROM player_match pm
        JOIN replays r ON r.id = pm.replay_id
        WHERE pm.toon_handle IN ({placeholders})
          AND r.mode = 'Storm League'
    """
    return store.conn.execute(sql, squad).fetchall()


def _display_name_lookup(store: Store, handles: tuple[str, ...]) -> dict[str, str]:
    if not handles:
        return {}
    placeholders = ",".join("?" for _ in handles)
    rows = store.conn.execute(
        f"SELECT toon_handle, display_name FROM players "
        f"WHERE toon_handle IN ({placeholders})",
        handles,
    ).fetchall()
    return {r["toon_handle"]: (r["display_name"] or "") for r in rows}


# --- per-section computation -------------------------------------------------


def _compute_window_summary(rows: list[Any], days: int,
                            start_iso: str, end_iso: str) -> WindowSummary:
    """Squad's overall record in the window.

    A "squad game" is one match the squad played, so we deduplicate by
    replay_id — five rows for a 5-stack must count as one.
    """
    by_replay: dict[int, int] = {}  # replay_id -> winning team-result for *any* squad member in it
    for r in rows:
        rid = int(r["replay_id"])
        # All squad members in the same replay share the same result
        # (they're all on the same team in stacked queue), but be safe
        # by majority-vote — first write wins, dups skipped.
        by_replay.setdefault(rid, int(r["result"]))
    games = len(by_replay)
    wins = sum(1 for res in by_replay.values() if res == 1)
    return WindowSummary(
        days=days, start_iso=start_iso, end_iso=end_iso,
        games=games, wins=wins,
    )


def _compute_players(rows: list[Any], display_names: dict[str, str]) -> list[PlayerWeekStats]:
    """Per-squad-member breakdown in the window."""
    by_handle: dict[str, dict[str, Any]] = {}
    for r in rows:
        h = r["toon_handle"]
        bucket = by_handle.setdefault(
            h, {"games": 0, "wins": 0,
                "k": 0, "d": 0, "a": 0,
                "by_hero": {}}
        )
        bucket["games"] += 1
        bucket["wins"] += 1 if int(r["result"]) == 1 else 0
        bucket["k"] += int(r["kills"] or 0)
        bucket["d"] += int(r["deaths"] or 0)
        bucket["a"] += int(r["assists"] or 0)
        hero = r["hero"] or "?"
        h2 = bucket["by_hero"].setdefault(hero, {"games": 0, "wins": 0})
        h2["games"] += 1
        h2["wins"] += 1 if int(r["result"]) == 1 else 0

    out: list[PlayerWeekStats] = []
    for handle, stats in by_handle.items():
        # Most-played hero — tiebreak by wins so a 4-2 hero beats a 0-6
        # hero on the same number of games.
        if stats["by_hero"]:
            hero_name, hero_stats = max(
                stats["by_hero"].items(),
                key=lambda kv: (kv[1]["games"], kv[1]["wins"]),
            )
        else:
            hero_name, hero_stats = "", {"games": 0, "wins": 0}
        games = stats["games"]
        out.append(
            PlayerWeekStats(
                toon_handle=handle,
                display_name=display_names.get(handle) or handle,
                games=games,
                wins=stats["wins"],
                avg_k=(stats["k"] / games) if games else 0.0,
                avg_d=(stats["d"] / games) if games else 0.0,
                avg_a=(stats["a"] / games) if games else 0.0,
                most_played_hero=hero_name,
                most_played_hero_games=hero_stats["games"],
                most_played_hero_wins=hero_stats["wins"],
            )
        )
    out.sort(key=lambda p: -p.games)
    return out


def _award_score(
    rows: list[Any], handle: str, *,
    field_name: str,
    roles: frozenset[str] | None = None,
    threshold: int = 0,
) -> tuple[float, int, str]:
    """Average of ``field_name`` for ``handle`` over the games that
    *count* for this award, plus the hero that contributed the most.

    Scoping (so a flex player isn't penalised for mixing roles):

    * ``roles`` — only games whose hero belongs to one of these roles
      count. This is the precise gate: 治疗最高 scopes to healer/support
      so a DPS player's near-zero healing games are excluded entirely,
      and a healer who also plays DPS contends on their healer games
      alone. When a hero's role is unknown (not in the role map) the
      game still counts **iff** it clears ``threshold`` — so an
      unmapped hero is never silently dropped.
    * ``threshold`` — fallback numeric gate, used for unmapped heroes
      and for un-scoped awards that still want to drop trivial values.

    The representative hero is the one with the **highest cumulative
    value** (ties broken by games), not the most-played — otherwise an
    award could be labelled with a hero who barely earned it (e.g. a
    high-pick-rate 阿兹莫丹 fronting the control award when the control
    actually came from a few 雷加尔 games).

    Returns ``(avg, games_counted, hero)``.
    """
    total = 0
    n = 0
    by_hero: dict[str, list[int]] = {}  # hero -> [total_value, games]
    for r in rows:
        if r["toon_handle"] != handle:
            continue
        hero = r["hero"] or "?"
        v = _sanitised(r[field_name])
        if roles is not None:
            role = hero_role(hero)
            if role is not None:
                if role not in roles:
                    continue
            elif v <= threshold:  # unknown role → numeric fallback gate
                continue
        elif threshold > 0 and v <= threshold:
            continue
        total += v
        n += 1
        slot = by_hero.setdefault(hero, [0, 0])
        slot[0] += v
        slot[1] += 1
    if n == 0:
        return 0.0, 0, ""
    hero = max(by_hero.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
    return total / n, n, hero


def _award_score_kda(rows: list[Any], handle: str) -> tuple[float, int, str]:
    """KDA over all of ``handle``'s games, plus their best-KDA-contributing
    hero (most K+A, ties broken by games) — consistent with the other
    awards showing the hero that earned the trophy."""
    k = d = a = 0
    n = 0
    by_hero: dict[str, list[int]] = {}  # hero -> [k+a, games]
    for r in rows:
        if r["toon_handle"] != handle:
            continue
        kk = int(r["kills"] or 0)
        aa = int(r["assists"] or 0)
        k += kk
        d += int(r["deaths"] or 0)
        a += aa
        n += 1
        hero = r["hero"] or "?"
        slot = by_hero.setdefault(hero, [0, 0])
        slot[0] += kk + aa
        slot[1] += 1
    if n == 0:
        return 0.0, 0, ""
    kda = (k + a) / max(d, 1)  # avoid div0; same convention as the rest of the app
    hero = max(by_hero.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
    return kda, n, hero


def _compute_awards(
    rows: list[Any],
    display_names: dict[str, str],
    handles: list[str],
) -> list[MvpAward]:
    """One winner per award, spread across the squad.

    Each award is ranked independently, then a subset is chosen for
    display so no single player holds more than ``_MAX_AWARDS_PER_PLAYER``
    (see :func:`_select_awards`) — always keeping each award's genuine
    #1, never demoting to a runner-up. Awards where nobody qualifies
    (e.g. a window with zero healer games → no 治疗最高) are dropped
    before selection. The award **order below is the display order**.

    Role scoping: many awards only make sense for certain hero roles, so
    they pass ``roles=`` and :func:`_award_score` counts only the games
    on a hero of that role (see its docstring). This means a flex player
    contends for 治疗最高 on their healer games alone, not diluted by
    their DPS games — and a DPS player's incidental healing never wins.
    Combat/objective awards every role contributes to stay un-scoped.
    """
    HEAL = _HEAL_ROLES        # healer + support
    FRONT = _FRONTLINE_ROLES  # tank + bruiser
    DMG = _DAMAGE_ROLES       # assassin + bruiser

    # (label, scoring) in display order. scoring(handle) -> (value, games, hero).
    specs: list[tuple[str, Any]] = [
        # --- combat: every role contributes, no role scope --------------
        ("ui.weekly.award.god_kda", lambda h: _award_score_kda(rows, h)),
        ("ui.weekly.award.teamfight", lambda h: _award_score(rows, h, field_name="takedowns")),
        # --- damage dealing: assassins & bruisers -----------------------
        ("ui.weekly.award.dmg_king", lambda h: _award_score(rows, h, field_name="hero_damage", roles=DMG)),
        ("ui.weekly.award.tf_dmg", lambda h: _award_score(rows, h, field_name="tf_dmg", roles=DMG)),
        ("ui.weekly.award.solo_kill", lambda h: _award_score(rows, h, field_name="solo_kills", roles=DMG)),
        ("ui.weekly.award.on_fire", lambda h: _award_score(rows, h, field_name="on_fire", roles=DMG)),
        # --- healing: healers & supports --------------------------------
        ("ui.weekly.award.healer", lambda h: _award_score(
            rows, h, field_name="healing", roles=HEAL, threshold=_HEAL_THRESHOLD)),
        ("ui.weekly.award.clutch", lambda h: _award_score(rows, h, field_name="clutch", roles=HEAL)),
        # --- frontline: tanks & bruisers --------------------------------
        ("ui.weekly.award.tank", lambda h: _award_score(
            rows, h, field_name="damage_taken", roles=FRONT, threshold=_TAKEN_THRESHOLD)),
        ("ui.weekly.award.soak", lambda h: _award_score(rows, h, field_name="damage_soaked", roles=FRONT)),
        # --- control / protection: frontline + support -----------------
        ("ui.weekly.award.cc", lambda h: _award_score(rows, h, field_name="cc", roles=FRONT | HEAL)),
        ("ui.weekly.award.stun", lambda h: _award_score(rows, h, field_name="stun", roles=FRONT | HEAL)),
        ("ui.weekly.award.protect", lambda h: _award_score(rows, h, field_name="protection", roles=FRONT | HEAL)),
        # --- objective / map: every role, no scope ----------------------
        ("ui.weekly.award.siege", lambda h: _award_score(rows, h, field_name="structure_damage")),
        ("ui.weekly.award.xp", lambda h: _award_score(rows, h, field_name="xp")),
        ("ui.weekly.award.mercs", lambda h: _award_score(rows, h, field_name="mercs")),
        ("ui.weekly.award.towers", lambda h: _award_score(rows, h, field_name="towers")),
        ("ui.weekly.award.escapes", lambda h: _award_score(rows, h, field_name="escapes")),
        # --- tongue-in-cheek --------------------------------------------
        ("ui.weekly.award.actor", lambda h: _award_score(rows, h, field_name="dead")),
        ("ui.weekly.award.loner", lambda h: _award_score(rows, h, field_name="outnumbered")),
    ]

    # Rank every award's contestants (drop games=0). ranked[label] is a
    # descending list of (value, games, handle, hero).
    ranked: dict[str, list[tuple[float, int, str, str]]] = {}
    for label, scoring in specs:
        contestants: list[tuple[float, int, str, str]] = []
        for h in handles:
            v, n, hero = scoring(h)
            if n <= 0:
                continue
            contestants.append((v, n, h, hero))
        if contestants:
            contestants.sort(key=lambda t: (-t[0], -t[1]))
            ranked[label] = contestants

    shown = _select_awards([label for label, _ in specs], ranked)

    awards: list[MvpAward] = []
    for label in (label for label, _ in specs):  # preserve display order
        won = shown.get(label)
        if won is None:
            continue
        v, n, h, hero = won
        awards.append(
            MvpAward(
                label_key=label,
                display_name=display_names.get(h) or h,
                hero=hero,
                value=v,
                games=n,
            )
        )
    return awards


def _award_dominance(ranked_award: list[tuple[float, int, str, str]]) -> float:
    """How decisively the #1 contestant leads — winner_value / next
    *different* contestant's value (``inf`` when they're the only one).
    Used to decide which of an over-represented player's awards are the
    most iconic and worth keeping."""
    if not ranked_award:
        return 0.0
    top_val, _, top_handle, _ = ranked_award[0]
    for val, _n, handle, _hero in ranked_award[1:]:
        if handle != top_handle:
            return top_val / val if val > 0 else float("inf")
    return float("inf")  # sole contestant


def _select_awards(
    order: list[str],
    ranked: dict[str, list[tuple[float, int, str, str]]],
) -> dict[str, tuple[float, int, str, str]]:
    """Choose *which* awards to display so trophies spread across the
    squad — without ever lying about a winner.

    Every shown award keeps its genuine #1 (``ranked[label][0]``); we
    never demote to a runner-up. The squad's highest-volume player tends
    to top many cumulative-stat awards, so to avoid them sweeping the
    board we cap each player at ``_MAX_AWARDS_PER_PLAYER`` and, for
    anyone over the cap, keep the awards they lead **most decisively**
    (largest margin over the runner-up) and simply omit the rest. An
    omitted award means that metric goes untrophied this week — better
    than handing "Damage king" to the second-best damage dealer.
    """
    # True winner of each award (rank 0).
    winners = {label: ranked[label][0] for label in order if label in ranked}

    by_player: dict[str, list[str]] = defaultdict(list)
    for label, (_v, _n, handle, _hero) in winners.items():
        by_player[handle].append(label)

    keep: set[str] = set()
    for _handle, labels in by_player.items():
        # Most decisive leads first; keep the top N, drop the rest.
        labels.sort(key=lambda lb: _award_dominance(ranked[lb]), reverse=True)
        keep.update(labels[:_MAX_AWARDS_PER_PLAYER])

    return {label: winners[label] for label in order if label in keep}


def _compute_highlights(
    rows: list[Any], display_names: dict[str, str], top_n: int = 3,
) -> list[HighlightMatch]:
    """Top N matches by (K+A) / max(D, 1) across all squad members."""
    scored: list[tuple[float, Any]] = []
    for r in rows:
        k = int(r["kills"] or 0)
        d = int(r["deaths"] or 0)
        a = int(r["assists"] or 0)
        kda = (k + a) / max(d, 1)
        scored.append((kda, r))
    scored.sort(key=lambda t: -t[0])
    out: list[HighlightMatch] = []
    for _, r in scored[:top_n]:
        out.append(
            HighlightMatch(
                played_at=r["played_at"],
                display_name=display_names.get(r["toon_handle"])
                             or (r["pm_display_name"] or ""),
                hero=r["hero"] or "?",
                map_name=r["map_name"] or "",
                result=int(r["result"]),
                kills=int(r["kills"] or 0),
                deaths=int(r["deaths"] or 0),
                assists=int(r["assists"] or 0),
                hero_damage=_sanitised(r["hero_damage"]),
            )
        )
    return out


def _compute_hero_pool(rows: list[Any]) -> tuple[list[HeroPickStat], list[HeroPickStat]]:
    """(top picked, top winrate). Same population — squad-side pick rows."""
    # Squad games per hero, deduplicated at the (replay_id, hero) level so
    # a stack with two members on the same hero — which can't happen in
    # HotS — wouldn't double-count. With one member per hero per replay
    # this is just len(rows-with-hero).
    by_hero: dict[str, dict[str, Any]] = {}
    for r in rows:
        hero = r["hero"] or "?"
        d = by_hero.setdefault(hero, {"games": 0, "wins": 0})
        d["games"] += 1
        d["wins"] += 1 if int(r["result"]) == 1 else 0

    flat = [HeroPickStat(hero=h, games=d["games"], wins=d["wins"])
            for h, d in by_hero.items()]
    top_picked = sorted(flat, key=lambda x: -x.games)[:5]
    qualifying = [h for h in flat if h.games >= _MIN_GAMES_HERO_TOP_WR]
    top_wr = sorted(qualifying, key=lambda x: (-x.winrate, -x.games))[:3]
    return top_picked, top_wr


def _compute_hero_combos(rows: list[Any]) -> list[HeroComboStat]:
    """Top squad hero *pairings* by winrate, computed over all history.

    Unlike the rest of the report, this is not scoped to the rolling
    window — ``rows`` is the squad's entire Storm League history so a
    combo's winrate reflects every game it was ever fielded.

    For each replay we collect the distinct squad heroes that played it,
    then count every unordered pair. A combo's record is "games the squad
    fielded both heroes together" → "of those, how many were won". Pairs
    are keyed on the sorted (hero_a, hero_b) tuple so pick order never
    splits a combo into two rows.

    Only pairings with at least ``_MIN_GAMES_HERO_COMBO`` games qualify —
    a combo needs to recur before its winrate means anything. Sorted by
    winrate desc, then games desc as the tiebreak so a 3-0 combo outranks
    a 2-0 one.
    """
    # replay_id -> {hero: result}. Same hero can't appear twice per team in
    # HotS, so a set of heroes per replay is exact.
    by_replay: dict[int, tuple[set[str], int]] = {}
    for r in rows:
        rid = int(r["replay_id"])
        hero = r["hero"] or "?"
        if hero == "?":
            continue
        heroes, _result = by_replay.setdefault(rid, (set(), int(r["result"])))
        heroes.add(hero)

    by_pair: dict[tuple[str, str], dict[str, int]] = {}
    for heroes, result in by_replay.values():
        ordered = sorted(heroes)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                key = (ordered[i], ordered[j])
                d = by_pair.setdefault(key, {"games": 0, "wins": 0})
                d["games"] += 1
                if result == 1:
                    d["wins"] += 1

    qualifying = [
        HeroComboStat(hero_a=a, hero_b=b, games=d["games"], wins=d["wins"])
        for (a, b), d in by_pair.items()
        if d["games"] >= _MIN_GAMES_HERO_COMBO
    ]
    qualifying.sort(key=lambda c: (-c.winrate, -c.games))
    return qualifying[:_MAX_HERO_COMBOS]


def _compute_maps(rows: list[Any]) -> list[MapStat]:
    """One row per map. Dedup across squad members on the same replay."""
    by_replay: dict[int, tuple[str, int]] = {}
    for r in rows:
        rid = int(r["replay_id"])
        if rid not in by_replay:
            by_replay[rid] = (r["map_name"] or "?", int(r["result"]))
    by_map: dict[str, dict[str, int]] = {}
    for map_name, result in by_replay.values():
        d = by_map.setdefault(map_name, {"games": 0, "wins": 0})
        d["games"] += 1
        if result == 1:
            d["wins"] += 1
    out = [MapStat(map_name=m, games=d["games"], wins=d["wins"])
           for m, d in by_map.items()]
    out.sort(key=lambda x: (-x.games, -x.winrate))
    return out


def _compute_streaks(rows: list[Any]) -> tuple[StreakRun, StreakRun]:
    """Longest consecutive win and loss runs for the squad in the window.

    Walks unique replays in chronological order — squad members all
    share the same outcome on the same replay, so we only need one
    sample per replay_id.
    """
    seen: dict[int, tuple[str, int]] = {}
    for r in rows:
        rid = int(r["replay_id"])
        if rid not in seen:
            seen[rid] = (r["played_at"], int(r["result"]))
    chrono = sorted(seen.values(), key=lambda t: t[0])

    longest_win = StreakRun(0, "", "")
    longest_loss = StreakRun(0, "", "")
    cur_kind: int | None = None
    cur_len = 0
    cur_start = ""
    cur_end = ""
    for played_at, result in chrono:
        kind = 1 if result == 1 else 0
        if kind == cur_kind:
            cur_len += 1
            cur_end = played_at
        else:
            cur_kind = kind
            cur_len = 1
            cur_start = played_at
            cur_end = played_at
        target = longest_win if kind == 1 else longest_loss
        if cur_len > target.length:
            run = StreakRun(cur_len, cur_start, cur_end)
            if kind == 1:
                longest_win = run
            else:
                longest_loss = run
    return longest_win, longest_loss


# --- public API --------------------------------------------------------------


def build_weekly_report(
    store: Store,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> WeeklyReport:
    """Compute every section of the weekly report in one pass.

    ``now`` is overridable so tests can pin the rolling window. The
    "previous week" baseline is the same-length window immediately
    preceding ``now - days``.
    """
    end_dt = (now or _now_utc())
    start_dt = end_dt - timedelta(days=days)
    prev_end_dt = start_dt
    prev_start_dt = prev_end_dt - timedelta(days=days)

    end_iso = end_dt.isoformat()
    start_iso = start_dt.isoformat()
    prev_end_iso = prev_end_dt.isoformat()
    prev_start_iso = prev_start_dt.isoformat()

    squad = tuple(store.squad_handles())
    display_names = _display_name_lookup(store, squad)

    cur_rows = _fetch_squad_matches(
        store, squad, start_iso=start_iso, end_iso=end_iso,
    )
    prev_rows = _fetch_squad_matches(
        store, squad, start_iso=prev_start_iso, end_iso=prev_end_iso,
    )

    overview = Overview(
        current=_compute_window_summary(
            cur_rows, days, start_iso, end_iso,
        ),
        previous=_compute_window_summary(
            prev_rows, days, prev_start_iso, prev_end_iso,
        ),
    )

    players = _compute_players(cur_rows, display_names)
    awards = _compute_awards(cur_rows, display_names, list(squad))
    highlights = _compute_highlights(cur_rows, display_names)
    top_picked, top_wr = _compute_hero_pool(cur_rows)
    # Hero combos span the squad's full history, not the rolling window.
    hero_combos = _compute_hero_combos(_fetch_squad_combo_rows(store, squad))
    maps = _compute_maps(cur_rows)
    win_streak, loss_streak = _compute_streaks(cur_rows)

    return WeeklyReport(
        overview=overview,
        players=players,
        awards=awards,
        highlights=highlights,
        hero_top_picked=top_picked,
        hero_top_winrate=top_wr,
        hero_combos=hero_combos,
        maps=maps,
        longest_win_streak=win_streak,
        longest_loss_streak=loss_streak,
    )


# --- text renderer -----------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x*100:.0f}%"


def _fmt_kda_avg(k: float, d: float, a: float) -> str:
    return f"{k:.1f}/{d:.1f}/{a:.1f}"


def _fmt_kda_int(k: int, d: int, a: int) -> str:
    return f"{k}/{d}/{a}"


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


def _fmt_date(iso: str) -> str:
    """``2026-05-15T20:14:00+00:00`` → ``05-15``. Empty stays empty."""
    if not iso:
        return ""
    return iso[5:10] if len(iso) >= 10 else iso


# Awards whose metric is a per-game average of seconds (control / stun /
# on-fire / dead time) — rendered as "78s" rather than a compact 'k' string.
_TIME_AWARDS = frozenset({"cc", "stun", "on_fire", "actor"})
# Awards whose metric is a small per-game count (takedowns / solo kills /
# mercs / towers / clutch heals / protection events / escapes / outnumbered
# deaths) — rendered with one decimal, e.g. "12.4".
_COUNT_AWARDS = frozenset({
    "teamfight", "solo_kill", "mercs", "towers", "clutch",
    "protect", "escapes", "loner",
})


def _award_value_str(label_key: str, value: float) -> str:
    """Format the award metric in the unit the user expects.

    KDA gets a 2-dp number; time awards get a seconds suffix; small-count
    awards get a 1-dp number; everything else gets a compact 'k' string.
    """
    suffix = label_key.rsplit(".", 1)[-1]
    if suffix == "god_kda":
        return f"{value:.2f}"
    if suffix in _TIME_AWARDS:
        return f"{value:.0f}s"
    if suffix in _COUNT_AWARDS:
        return f"{value:.1f}"
    return _fmt_k(value)


def format_weekly_brief(report: WeeklyReport) -> str:
    """Render the report as plain text suitable for clipboard / chat paste."""
    days = report.overview.current.days
    if report.overview.current.games == 0:
        # Strip the HTML hint from the empty key for the plain-text output.
        return (
            t("ui.weekly.title", days=days)
            + "\n"
            + (
                t("ui.weekly.empty", days=days)
                .replace("<i style='color:#a88;'>", "")
                .replace("</i>", "")
            )
        )

    blocks: list[list[str]] = []

    header = [
        t("ui.weekly.title", days=days),
        t(
            "ui.weekly.window_line",
            start=_fmt_date(report.overview.current.start_iso),
            end=_fmt_date(report.overview.current.end_iso),
        ),
    ]
    blocks.append(header)

    # Overview
    cur, prev = report.overview.current, report.overview.previous
    overview_lines = [
        t("ui.weekly.section.overview"),
        "  " + t(
            "ui.weekly.overview_line",
            games=cur.games, wr=_fmt_pct(cur.winrate),
            prev_games=prev.games, prev_wr=_fmt_pct(prev.winrate),
        ),
        "  " + t(
            "ui.weekly.overview_delta",
            games_delta=report.overview.games_delta,
            wr_delta=report.overview.winrate_delta_pp,
        ),
    ]
    blocks.append(overview_lines)

    # Players
    if report.players:
        player_lines = [t("ui.weekly.section.players")]
        for p in report.players:
            kda = _fmt_kda_avg(p.avg_k, p.avg_d, p.avg_a)
            if p.most_played_hero:
                player_lines.append(
                    "  - " + t(
                        "ui.weekly.player_line",
                        name=p.display_name,
                        games=p.games, wr=_fmt_pct(p.winrate),
                        kda=kda,
                        hero=p.most_played_hero,
                        hero_wins=p.most_played_hero_wins,
                        hero_games=p.most_played_hero_games,
                    )
                )
            else:
                player_lines.append(
                    "  - " + t(
                        "ui.weekly.player_line_no_hero",
                        name=p.display_name,
                        games=p.games, wr=_fmt_pct(p.winrate),
                        kda=kda,
                    )
                )
        blocks.append(player_lines)

    # Awards
    if report.awards:
        award_lines = [t("ui.weekly.section.awards")]
        for a in report.awards:
            award_lines.append(
                "  - " + t(
                    "ui.weekly.award_line",
                    label=t(a.label_key),
                    name=a.display_name,
                    hero=a.hero or "?",
                    value=_award_value_str(a.label_key, a.value),
                    games=a.games,
                )
            )
        blocks.append(award_lines)

    # Highlights
    if report.highlights:
        hi_lines = [t("ui.weekly.section.highlights")]
        for h in report.highlights:
            result_word = t(
                "ui.weekly.match_won" if h.result == 1
                else "ui.weekly.match_lost"
            )
            hi_lines.append(
                "  - " + t(
                    "ui.weekly.highlight_line",
                    when=_fmt_date(h.played_at),
                    name=h.display_name or "?",
                    hero=h.hero,
                    map=h.map_name or "?",
                    result=result_word,
                    kda=_fmt_kda_int(h.kills, h.deaths, h.assists),
                    hd=_fmt_k(h.hero_damage),
                )
            )
        blocks.append(hi_lines)

    # Heroes
    if report.hero_top_picked or report.hero_top_winrate:
        h_lines = [t("ui.weekly.section.heroes")]
        if report.hero_top_picked:
            chips = [
                t(
                    "ui.weekly.hero_chip",
                    hero=h.hero, wins=h.wins, games=h.games,
                    wr=_fmt_pct(h.winrate),
                )
                for h in report.hero_top_picked
            ]
            h_lines.append("  " + t("ui.weekly.heroes_top_picked")
                           + " " + " · ".join(chips))
        if report.hero_top_winrate:
            chips = [
                t(
                    "ui.weekly.hero_chip",
                    hero=h.hero, wins=h.wins, games=h.games,
                    wr=_fmt_pct(h.winrate),
                )
                for h in report.hero_top_winrate
            ]
            h_lines.append("  " + t("ui.weekly.heroes_top_wr")
                           + " " + " · ".join(chips))
        blocks.append(h_lines)

    # Hero combos
    if report.hero_combos:
        c_lines = [t("ui.weekly.section.combos")]
        for c in report.hero_combos:
            c_lines.append(
                "  - " + t(
                    "ui.weekly.combo_line",
                    hero_a=c.hero_a, hero_b=c.hero_b,
                    wins=c.wins, games=c.games,
                    wr=_fmt_pct(c.winrate),
                )
            )
        blocks.append(c_lines)

    # Maps
    if report.maps:
        m_lines = [t("ui.weekly.section.maps")]
        for m in report.maps:
            m_lines.append(
                "  - " + t(
                    "ui.weekly.map_line",
                    map=m.map_name or "?",
                    wins=m.wins, games=m.games,
                    wr=_fmt_pct(m.winrate),
                )
            )
        blocks.append(m_lines)

    # Streaks
    s_lines = [t("ui.weekly.section.streaks")]
    if report.longest_win_streak.is_empty:
        s_lines.append("  " + t("ui.weekly.streak_none_win"))
    else:
        run = report.longest_win_streak
        s_lines.append(
            "  " + t(
                "ui.weekly.streak_win",
                n=run.length,
                start=_fmt_date(run.started_at),
                end=_fmt_date(run.ended_at),
            )
        )
    if report.longest_loss_streak.is_empty:
        s_lines.append("  " + t("ui.weekly.streak_none_loss"))
    else:
        run = report.longest_loss_streak
        s_lines.append(
            "  " + t(
                "ui.weekly.streak_loss",
                n=run.length,
                start=_fmt_date(run.started_at),
                end=_fmt_date(run.ended_at),
            )
        )
    blocks.append(s_lines)

    return "\n".join("\n".join(block) for block in blocks)
