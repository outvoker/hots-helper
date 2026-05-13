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

import re

from .db import Store
from .stats import two_proportion_z_test, wilson_lower_bound


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
    alpha: float = 0.25,
    wilson_threshold: float = 0.50,
) -> list[ThreatHero]:
    """Heroes this player plays unusually well.

    ``mode``:
    - ``"relative"``: flag heroes where winrate is meaningfully higher than
      this player's winrate on *other* heroes. Matches the BP question
      "what's this specific player dangerous on?" regardless of whether
      they're a strong player overall.
    - ``"absolute"``: keep only heroes whose Wilson LB is ≥ ``wilson_threshold``.
      Useful when you already know the player is good and want to filter to
      their sure-fire picks.
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
        other_games = max(0, total_games - games)
        other_wins = max(0, total_wins - wins)
        test = two_proportion_z_test(wins, games, other_wins, other_games)

        if mode == "relative":
            # Significant positive lift vs. this player's baseline.
            if not (test.p_value < alpha and test.lift > 0):
                # Fallback: if the player has very few other games, the z-test
                # lacks power — admit the hero on a reasonable Wilson cut.
                if other_games < min_games and wlb >= wilson_threshold:
                    pass
                else:
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
    min_games: int = 3,
    alpha: float = 0.25,
    wilson_threshold: float = 0.50,
    per_player_top: int = 5,
    mode: str = "relative",
) -> list[OpponentProfile]:
    """One profile per opponent name with their top threat heroes."""
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
        out.append(
            OpponentProfile(
                name_searched=name,
                toon_handle=best_handle or "",
                display_name=best_display,
                total_games=best_games if best_games >= 0 else 0,
                threats=threats,
                note="" if threats else "no signature heroes yet",
            )
        )
    return out


def recommend_bans(
    store: Store,
    opponent_names: list[str],
    *,
    min_games: int = 3,
    alpha: float = 0.25,
    wilson_threshold: float = 0.50,
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
    min_games: int = 5,
    alpha: float = 0.10,
    top: int = 5,
    exclude_heroes: set[str] | None = None,
) -> list[PickCandidate]:
    """Heroes to consider picking on this map.

    A hero makes the list if:
    - it has been played on this map at least ``min_games`` times in the DB,
    - either its map winrate is significantly higher than its global winrate,
    - or its Wilson lower bound on this map is above 50%.
    Results are ranked by Wilson lower bound.
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
        significant_positive = test.p_value < alpha and test.lift > 0
        passes = significant_positive or wlb >= 0.50
        if not passes:
            continue

        cand = PickCandidate(
            hero=hero,
            map_games=m_games,
            map_wins=m_wins,
            map_winrate=m_wins / m_games if m_games else 0.0,
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
