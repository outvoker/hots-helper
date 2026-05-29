"""Ban / pick recommendations for the draft phase.

Two independent concerns:

- **Ban**: given the 5 opponent display names, find heroes any of them play
  unusually well. Merge across opponents so that a hero multiple people
  can pick rises to the top.
- **Pick**: given the map, suggest heroes with a statistically positive map
  winrate, plus the recommended talent build for each.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import re

from .db import Store
from .stats import two_proportion_z_test, wilson_lower_bound

if TYPE_CHECKING:
    from .player_rank import PlayerRankRow


_AI_NAME_PATTERNS = (
    re.compile(r"^玩家\d+$"),
    re.compile(r"^player\d+$", re.IGNORECASE),
    re.compile(r"^bot\d*$", re.IGNORECASE),
)


def _looks_like_ai(name: str) -> bool:
    name = name.strip()
    return any(p.match(name) for p in _AI_NAME_PATTERNS)


# --- dataclasses --------------------------------------------------------------


@dataclass
class ThreatHero:
    """One hero this player plays well."""
    hero: str
    hero_id: str
    games: int
    wins: int
    raw_winrate: float
    wilson_lb: float
    avg_k: float
    avg_d: float
    avg_a: float
    last_played: str
    # How much better than this player's baseline this hero is.
    # Expressed as (hero_wr - player_other_heroes_wr) in percentage points.
    lift_pp: float = 0.0
    # Two-sided p-value of the lift.
    p_value: float = 1.0


@dataclass
class OpponentProfile:
    name_searched: str
    toon_handle: str
    display_name: str
    total_games: int
    threats: list[ThreatHero] = field(default_factory=list)
    note: str = ""
    # Composite combat-power percentile (0..100). 0.0 when we don't have
    # a baseline yet for this handle (too few matches with the squad).
    power: float = 0.0
    power_rank: int = 0          # 1-indexed in the global ranking
    power_total: int = 0         # population size of the ranking
    # How often this handle has lined up with vs against the squad in our
    # local DB. Both counts are zero when the player has never crossed
    # paths with the 5-stack (e.g. the squad is undefined or the player
    # is brand-new).
    ally_games: int = 0
    ally_wins: int = 0
    enemy_games: int = 0
    enemy_wins: int = 0


@dataclass
class BanCandidate:
    hero: str
    hero_id: str
    # Threat score we use for ranking.
    score: float
    # Who on the enemy team plays it (with their Wilson LB on the hero).
    contributors: list[tuple[str, int, int, float]] = field(default_factory=list)
    # Summary for display.
    total_games: int = 0
    total_wins: int = 0

    @property
    def combined_wr(self) -> float:
        return (self.total_wins / self.total_games) if self.total_games else 0.0


@dataclass
class TalentPick:
    tier: int
    talent: str
    games: int
    wins: int
    pick_rate: float   # within the tier
    wilson_lb: float


@dataclass
class PickCandidate:
    hero: str
    map_games: int
    map_wins: int
    map_winrate: float
    map_wilson_lb: float
    global_games: int
    global_wins: int
    global_winrate: float
    lift_pp: float        # map_winrate - global_winrate in percentage points
    p_value: float
    significant: bool
    # Recommended build: the highest-WLB talent per tier.
    recommended_build: list[TalentPick] = field(default_factory=list)


# --- ban side -----------------------------------------------------------------


def _threats_for_handle(
    store: Store,
    toon_handle: str,
    *,
    min_games: int,
    mode: str = "relative",
    alpha: float = 0.40,
    wilson_threshold: float = 0.40,
) -> list[ThreatHero]:
    """Heroes this player plays unusually well.

    ``mode``:
    - ``"relative"``: flag heroes where winrate is meaningfully higher than
      this player's winrate on *other* heroes.
    - ``"absolute"``: keep only heroes whose Wilson LB is ≥ ``wilson_threshold``.

    Defaults are deliberately permissive because most opponents only show up
    in the local DB 2-5 times — strict significance never passes. We err on
    the side of giving the user some signal: any hero played at least
    ``min_games`` times with a non-bad winrate is at least worth knowing.
    """
    hero_rows = list(store.hero_stats_for_handle(toon_handle))
    # Player baseline: total wins and games on everything other than the hero
    # we're evaluating. Pre-aggregate totals so we can subtract per-row.
    total_games = sum(int(r["games"]) for r in hero_rows)
    total_wins = sum(int(r["wins"] or 0) for r in hero_rows)

    threats: list[ThreatHero] = []
    for row in hero_rows:
        games = int(row["games"])
        wins = int(row["wins"] or 0)
        if games < min_games:
            continue

        wlb = wilson_lower_bound(wins, games)
        raw_wr = (wins / games) if games else 0.0
        other_games = max(0, total_games - games)
        other_wins = max(0, total_wins - wins)
        test = two_proportion_z_test(wins, games, other_wins, other_games)

        if mode == "relative":
            # Pass if any of:
            #  (a) statistically positive lift vs. the player's baseline,
            #  (b) the player has too few "other" games for the test to mean
            #      anything but their hero winrate is decent on its own,
            #  (c) raw winrate on this hero >= 60% (small sample but the
            #      pattern is unlikely to be pure luck — better to surface
            #      it than to hide it).
            sig_lift = test.p_value < alpha and test.lift > 0
            small_sample_strong = (
                other_games < min_games and wlb >= wilson_threshold
            )
            empirical_strong = raw_wr >= 0.60
            if not (sig_lift or small_sample_strong or empirical_strong):
                continue
        else:  # absolute
            if wlb < wilson_threshold:
                continue

        threats.append(
            ThreatHero(
                hero=row["hero"],
                hero_id=row["hero_id"] or "",
                games=games,
                wins=wins,
                raw_winrate=(wins / games) if games else 0.0,
                wilson_lb=wlb,
                avg_k=float(row["avg_k"] or 0.0),
                avg_d=float(row["avg_d"] or 0.0),
                avg_a=float(row["avg_a"] or 0.0),
                last_played=row["last_played"],
                lift_pp=test.lift * 100,
                p_value=test.p_value,
            )
        )
    # Rank by lift first (this is what "unusually well" means), ties broken
    # by Wilson LB so we don't promote noisy tiny samples.
    threats.sort(key=lambda t: (-t.lift_pp, -t.wilson_lb, -t.games))
    return threats


def profile_opponents(
    store: Store,
    names: list[str],
    *,
    min_games: int = 2,
    alpha: float = 0.40,
    wilson_threshold: float = 0.40,
    per_player_top: int = 5,
    mode: str = "relative",
    rank_index: dict[str, "PlayerRankRow"] | None = None,
    rank_total: int = 0,
    squad_handles: tuple[str, ...] | None = None,
) -> list[OpponentProfile]:
    """One profile per opponent name with their top threat heroes.

    ``rank_index`` and ``rank_total`` come from
    :func:`player_rank.compute_player_rankings` and let us attach a
    combat-power percentile to each profile. ``squad_handles`` lets us
    split shared games into ally vs enemy counts. All three are
    optional; when omitted the extra fields stay at their zero defaults.
    """
    out: list[OpponentProfile] = []
    for name in names:
        if _looks_like_ai(name):
            out.append(
                OpponentProfile(
                    name_searched=name,
                    toon_handle="",
                    display_name=name,
                    total_games=0,
                    note="appears to be an AI slot, skipped",
                )
            )
            continue
        rows = store.find_players_by_name(name)
        if not rows:
            out.append(
                OpponentProfile(
                    name_searched=name,
                    toon_handle="",
                    display_name=name,
                    total_games=0,
                    note="not found in local DB",
                )
            )
            continue
        # Pick the most-active matching handle if duplicates exist.
        best_handle = None
        best_games = -1
        best_display = name
        for row in rows:
            handle = row["toon_handle"]
            overall = store.overall_for_handle(handle)
            games = int(overall["games"] or 0) if overall else 0
            if games > best_games:
                best_games = games
                best_handle = handle
                best_display = row["display_name"]

        threats = _threats_for_handle(
            store,
            best_handle,
            min_games=min_games,
            mode=mode,
            alpha=alpha,
            wilson_threshold=wilson_threshold,
        )[:per_player_top]

        power = 0.0
        power_rank = 0
        if rank_index is not None and best_handle:
            ranked = rank_index.get(best_handle)
            if ranked is not None:
                power = ranked.power
                power_rank = ranked.rank

        ally = ally_w = enemy = enemy_w = 0
        if squad_handles and best_handle:
            split = store.side_split_vs_squad(best_handle, squad_handles)
            ally, ally_w = split["ally"], split["ally_wins"]
            enemy, enemy_w = split["enemy"], split["enemy_wins"]

        out.append(
            OpponentProfile(
                name_searched=name,
                toon_handle=best_handle or "",
                display_name=best_display,
                total_games=best_games if best_games >= 0 else 0,
                threats=threats,
                note="" if threats else "no signature heroes yet",
                power=power,
                power_rank=power_rank,
                power_total=rank_total,
                ally_games=ally,
                ally_wins=ally_w,
                enemy_games=enemy,
                enemy_wins=enemy_w,
            )
        )
    return out


def recommend_bans(
    store: Store,
    opponent_names: list[str],
    *,
    min_games: int = 2,
    alpha: float = 0.40,
    wilson_threshold: float = 0.40,
    top: int = 5,
    already_banned: set[str] | None = None,
    mode: str = "relative",
) -> list[BanCandidate]:
    """Cross-opponent ban ranking.

    Score combines each contributing opponent's signal on the hero. A hero
    that two opponents play unusually well outranks one only a single player
    is strong on, even if the single-player signal is stronger.

    Per-contributor signal defaults to the player's lift vs. their own
    baseline (``mode="relative"``). For "absolute" mode we score by Wilson
    lower bound instead.
    """
    banned = {b.lower() for b in (already_banned or set())}
    profiles = profile_opponents(
        store,
        opponent_names,
        min_games=min_games,
        alpha=alpha,
        wilson_threshold=wilson_threshold,
        per_player_top=20,
        mode=mode,
    )

    by_hero: dict[str, BanCandidate] = {}
    for prof in profiles:
        for t in prof.threats:
            if t.hero.lower() in banned:
                continue
            cand = by_hero.setdefault(
                t.hero,
                BanCandidate(hero=t.hero, hero_id=t.hero_id, score=0.0),
            )
            # Signal per contributor:
            #   relative mode: raw winrate on the hero, weighted by
            #     (1 - p_value) so uncertain lifts contribute less.
            #   absolute mode: Wilson lower bound.
            if mode == "relative":
                confidence = max(0.0, 1.0 - t.p_value)
                cand.score += t.raw_winrate * confidence
            else:
                cand.score += t.wilson_lb
            cand.total_games += t.games
            cand.total_wins += t.wins
            cand.contributors.append(
                (prof.display_name or prof.name_searched, t.games, t.wins, t.wilson_lb)
            )

    out = list(by_hero.values())
    out.sort(key=lambda c: (-c.score, -c.total_games))
    return out[:top]


# --- map-tier ban candidates --------------------------------------------------


@dataclass
class MapTierBan:
    """A hero that is statistically strong on this map and which our squad
    can't reliably counter (because we never play it ourselves)."""
    hero: str
    map_games: int
    map_wins: int
    map_winrate: float
    map_wilson_lb: float
    global_winrate: float
    lift_pp: float
    p_value: float
    squad_games_on_hero: int   # how many times the squad has picked this hero


def recommend_map_strong_bans(
    store: Store,
    map_name: str,
    squad_handles: list[str],
    *,
    min_games: int = 3,
    min_wlb: float = 0.40,
    squad_max_games: int = 5,
    top: int = 5,
    already_banned: set[str] | None = None,
) -> list[MapTierBan]:
    """Heroes that are strong on this map AND that we don't play.

    Pure statistical view of the map: hero hit at least ``min_games`` games
    on this map with Wilson LB ≥ ``min_wlb``, AND the squad has touched
    that hero in at most ``squad_max_games`` games (so even if we don't ban
    we can't reliably first-pick or counter it ourselves).
    """
    banned = {b.lower() for b in (already_banned or set())}
    raw = store.map_hero_winrates(map_name)
    out: list[MapTierBan] = []
    handle_set = set(squad_handles)
    for r in raw:
        hero = r["hero"]
        if hero.lower() in banned:
            continue
        m_games = int(r["games"])
        if m_games < min_games:
            continue
        m_wins = int(r["wins"] or 0)
        wlb = wilson_lower_bound(m_wins, m_games)
        if wlb < min_wlb:
            continue

        # How often has the squad played this hero?
        placeholders = ",".join("?" for _ in handle_set)
        squad_games = store.conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM player_match pm JOIN replays rp ON rp.id = pm.replay_id
            WHERE pm.hero = ?
              AND pm.toon_handle IN ({placeholders})
              AND rp.mode = 'Storm League'
            """,
            (hero, *handle_set),
        ).fetchone()
        squad_n = int(squad_games["n"] or 0)
        if squad_n > squad_max_games:
            continue

        g_games, g_wins = store.global_hero_winrate(hero)
        other_g = max(0, g_games - m_games)
        other_w = max(0, g_wins - m_wins)
        test = two_proportion_z_test(m_wins, m_games, other_w, other_g)

        out.append(
            MapTierBan(
                hero=hero,
                map_games=m_games,
                map_wins=m_wins,
                map_winrate=m_wins / m_games,
                map_wilson_lb=wlb,
                global_winrate=g_wins / g_games if g_games else 0.0,
                lift_pp=test.lift * 100,
                p_value=test.p_value,
                squad_games_on_hero=squad_n,
            )
        )
    out.sort(key=lambda c: (-c.map_wilson_lb, -c.map_games))
    return out[:top]


# --- pick side ----------------------------------------------------------------


def _recommended_build_for(store: Store, hero: str, map_name: str) -> list[TalentPick]:
    """Per-tier: the highest Wilson-LB talent that has at least one game."""
    talents = store.hero_talents(hero, map_name=map_name)
    if not talents:
        # Fall back to global talent data if the hero has never been seen on
        # this map.
        talents = store.hero_talents(hero)

    by_tier: dict[int, list[dict]] = {}
    for r in talents:
        by_tier.setdefault(r["tier"], []).append(dict(r))
    recs: list[TalentPick] = []
    for tier in sorted(by_tier):
        choices = by_tier[tier]
        total = sum(int(c["games"]) for c in choices) or 1
        best = max(
            choices,
            key=lambda c: (wilson_lower_bound(int(c["wins"]), int(c["games"])), int(c["games"])),
        )
        g = int(best["games"])
        w = int(best["wins"])
        recs.append(
            TalentPick(
                tier=tier,
                talent=best["talent"],
                games=g,
                wins=w,
                pick_rate=g / total,
                wilson_lb=wilson_lower_bound(w, g),
            )
        )
    return recs


def recommend_picks(
    store: Store,
    map_name: str,
    *,
    min_games: int = 3,
    alpha: float = 0.20,
    top: int = 8,
    min_wlb: float = 0.40,
    exclude_heroes: set[str] | None = None,
) -> list[PickCandidate]:
    """Heroes to consider picking on this map.

    A hero makes the list if it has been played on this map at least
    ``min_games`` times AND any of:
    - map winrate is significantly higher than global winrate (p < alpha), or
    - map Wilson lower bound is at least ``min_wlb`` (default 40%), or
    - map raw winrate >= 60% with at least min_games games (small-sample
      empirical strong pick).

    Results are ranked by map Wilson lower bound. Defaults are deliberately
    permissive: the local DB is too thin for strict significance to ever
    pass on most maps. Better to give the user a slightly noisy list than
    nothing at all.
    """
    exclude = {h.lower() for h in (exclude_heroes or set())}
    raw = store.map_hero_winrates(map_name)
    candidates: list[PickCandidate] = []
    for r in raw:
        hero = r["hero"]
        if hero.lower() in exclude:
            continue
        m_games = int(r["games"])
        if m_games < min_games:
            continue
        m_wins = int(r["wins"] or 0)
        g_games, g_wins = store.global_hero_winrate(hero)
        other_games = max(0, g_games - m_games)
        other_wins = max(0, g_wins - m_wins)
        test = two_proportion_z_test(m_wins, m_games, other_wins, other_games)
        wlb = wilson_lower_bound(m_wins, m_games)
        raw_wr = m_wins / m_games if m_games else 0.0

        significant_positive = test.p_value < alpha and test.lift > 0
        passes = (
            significant_positive
            or wlb >= min_wlb
            or raw_wr >= 0.60
        )
        if not passes:
            continue

        cand = PickCandidate(
            hero=hero,
            map_games=m_games,
            map_wins=m_wins,
            map_winrate=raw_wr,
            map_wilson_lb=wlb,
            global_games=g_games,
            global_wins=g_wins,
            global_winrate=g_wins / g_games if g_games else 0.0,
            lift_pp=test.lift * 100,
            p_value=test.p_value,
            significant=significant_positive,
            recommended_build=_recommended_build_for(store, hero, map_name),
        )
        candidates.append(cand)

    candidates.sort(key=lambda c: (-c.map_wilson_lb, -c.map_games))
    return candidates[:top]
