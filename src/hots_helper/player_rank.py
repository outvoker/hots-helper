"""Cross-game player rankings — four boards keyed on "playing with us"
vs "playing against us", each ordered by Wilson lower bound on win-rate.

Boards:

* ``worst_teammate``   — lowest WR when on our team
* ``best_teammate``    — highest WR when on our team
* ``best_opponent``    — highest WR when on the enemy team (i.e. they
                         beat us a lot — boogeymen)
* ``worst_opponent``   — lowest WR when on the enemy team (free wins)

Squad-side detection is a heuristic (anyone with ≥20 games of replay
history is treated as one of us, since random matchmaking opponents
almost never reappear that often). The boards exclude the squad
itself.

The four boards use raw WR sorting only as a tiebreaker; the primary
key is Wilson 95% lower bound so a 1-game 100% streak can't lead any
chart.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .db import Store
from .stats import wilson_lower_bound


# Board identifiers used by the dialog dropdown and the BP popup
# highlighter. Kept in one place so adding a fifth board (say,
# "highest avg damage") is one edit.
BOARD_WORST_TEAMMATE = "worst_teammate"
BOARD_BEST_TEAMMATE = "best_teammate"
BOARD_BEST_OPPONENT = "best_opponent"
BOARD_WORST_OPPONENT = "worst_opponent"

ALL_BOARDS = (
    BOARD_WORST_TEAMMATE,
    BOARD_BEST_TEAMMATE,
    BOARD_BEST_OPPONENT,
    BOARD_WORST_OPPONENT,
)


@dataclass(frozen=True)
class PlayerRankRow:
    """One row of any of the four boards."""
    rank: int                # 1-indexed within the board
    toon_handle: str
    display_name: str
    games: int
    wins: int
    win_rate: float          # 0..1
    wilson_lb: float         # 0..1, lower bound at 95%
    avg_k: float
    avg_d: float
    avg_a: float
    avg_hero_dmg: float
    avg_siege_dmg: float
    avg_structure_dmg: float
    avg_healing: float
    avg_dmg_taken: float
    avg_dmg_soaked: float
    avg_xp: float
    avg_cc: float
    last_seen_at: str
    # Composite "power" score in 0..100 (per-board population rescaling
    # is applied in :func:`_score_population`).
    power: float = 0.0

    @property
    def kda(self) -> float:
        return (self.avg_k + self.avg_a) / max(self.avg_d, 1.0)


def _row_to_rank(row, *, rank: int) -> PlayerRankRow:
    games = int(row["games"] or 0)
    wins = int(row["wins"] or 0)
    return PlayerRankRow(
        rank=rank,
        toon_handle=row["toon_handle"],
        display_name=row["display_name"] or "",
        games=games,
        wins=wins,
        win_rate=(wins / games) if games else 0.0,
        wilson_lb=wilson_lower_bound(wins, games),
        avg_k=float(row["avg_k"] or 0.0),
        avg_d=float(row["avg_d"] or 0.0),
        avg_a=float(row["avg_a"] or 0.0),
        avg_hero_dmg=float(row["avg_hero_dmg"] or 0.0),
        avg_siege_dmg=float(row["avg_siege_dmg"] or 0.0),
        avg_structure_dmg=float(row["avg_structure_dmg"] or 0.0),
        avg_healing=float(row["avg_healing"] or 0.0),
        avg_dmg_taken=float(row["avg_dmg_taken"] or 0.0),
        avg_dmg_soaked=float(row["avg_dmg_soaked"] or 0.0),
        avg_xp=float(row["avg_xp"] or 0.0),
        avg_cc=float(row["avg_cc"] or 0.0),
        last_seen_at=row["last_seen_at"] or "",
    )


# Sort modes the dialog dropdown can pick. Default is "wlb"
# (Wilson lower bound on win-rate, the original behaviour); "power"
# adds a composite combat-rating score across multiple stats.
SORT_WLB = "wlb"
SORT_POWER = "power"
ALL_SORTS = (SORT_WLB, SORT_POWER)


def _percentile_in(sorted_pop: list[float], v: float) -> float:
    """Percentile rank of ``v`` in a pre-sorted population, in 0..1.

    Uses ``bisect_right`` so identical values count as "at or below",
    matching the intuition "how many of the population did I beat".
    Empty / degenerate populations fall back to 0.5 (neutral).
    """
    n = len(sorted_pop)
    if n == 0:
        return 0.5
    return bisect_right(sorted_pop, v) / n


@dataclass(frozen=True)
class PowerBaseline:
    """Pre-sorted samples we use to compute percentile ranks.

    Built once per dialog refresh / BP analysis pass and shared across
    every player + every hero we score. The population is the *whole*
    DB (filtered by Storm League by default), so a sparse leaderboard
    can't artificially inflate scores — laolang's 22k hero damage
    sits at percentile ~0.15 globally instead of 1.0 because they're
    the only person on their board.
    """
    hero_damage: list[float]
    siege_damage: list[float]
    structure_damage: list[float]
    healing: list[float]
    damage_soaked: list[float]
    xp: list[float]
    cc: list[float]
    kda: list[float]                  # per-match KDA = (K+A)/max(D,1)
    deaths: list[float]               # raw deaths (smaller = better)
    win_rates_per_match: list[float]  # 0 or 1 per match — for WR percentile


def _build_baseline_from_rows(rows) -> PowerBaseline:
    hero, siege, struct, heal, soak, xp, cc, kda, deaths, wr = (
        [], [], [], [], [], [], [], [], [], [],
    )
    for r in rows:
        d = max(float(r["deaths"] or 0.0), 1.0)
        kda.append((float(r["kills"] or 0) + float(r["assists"] or 0)) / d)
        hero.append(float(r["hero_damage"] or 0))
        siege.append(float(r["siege_damage"] or 0))
        struct.append(float(r["structure_damage"] or 0))
        heal.append(float(r["healing"] or 0))
        soak.append(float(r["damage_soaked"] or 0))
        xp.append(float(r["xp"] or 0))
        cc.append(float(r["cc"] or 0))
        deaths.append(float(r["deaths"] or 0))
        wr.append(1.0 if int(r["result"] or 0) == 1 else 0.0)
    for lst in (hero, siege, struct, heal, soak, xp, cc, kda, deaths, wr):
        lst.sort()
    return PowerBaseline(
        hero_damage=hero,
        siege_damage=siege,
        structure_damage=struct,
        healing=heal,
        damage_soaked=soak,
        xp=xp,
        cc=cc,
        kda=kda,
        deaths=deaths,
        win_rates_per_match=wr,
    )


def build_power_baseline(store: Store) -> PowerBaseline:
    """Pull the global per-match population once. ~1ms per 1k rows."""
    rows = store.per_match_metric_samples()
    return _build_baseline_from_rows(rows)


# Weights for the "combat power" score. Tuned by feel: KDA + win rate
# carry the most weight (they're the cleanest signals of "this player
# does the right thing in fights"); damage / soak / xp / cc fill in
# the rest. Each component is a percentile rank against the global
# baseline so the weights are about *relative importance*, not units.
#
# Self-healing is excluded — it's mostly inflated by self-sustain
# heroes' baseline regen and doesn't track player skill well.
_POWER_WEIGHTS: dict[str, float] = {
    "win_rate":          0.30,
    "kda":               0.20,
    "hero_damage":       0.12,
    "siege_damage":      0.05,
    "structure_damage":  0.04,
    "healing":           0.08,
    "damage_soaked":     0.06,
    "experience":        0.07,
    "cc":                0.04,
    "deaths_inverse":    0.04,  # lower deaths = higher percentile
}


def power_score(
    *,
    baseline: PowerBaseline,
    win_rate: float,
    avg_k: float,
    avg_d: float,
    avg_a: float,
    avg_hero_dmg: float,
    avg_siege_dmg: float,
    avg_structure_dmg: float,
    avg_healing: float,
    avg_dmg_soaked: float,
    avg_xp: float,
    avg_cc: float,
) -> float:
    """0..100 composite score, percentile-ranked vs ``baseline``.

    Designed to be called from any context that has a per-player or
    per-hero average — the leaderboard, the hero ranking dialog, and
    eventually the BP popup cards. All inputs are *averages over
    matches*; the function does no aggregation.
    """
    kda = (avg_k + avg_a) / max(avg_d, 1.0)
    score = 0.0
    score += _POWER_WEIGHTS["win_rate"] * _percentile_in(
        baseline.win_rates_per_match, win_rate
    )
    score += _POWER_WEIGHTS["kda"] * _percentile_in(baseline.kda, kda)
    score += _POWER_WEIGHTS["hero_damage"] * _percentile_in(
        baseline.hero_damage, avg_hero_dmg
    )
    score += _POWER_WEIGHTS["siege_damage"] * _percentile_in(
        baseline.siege_damage, avg_siege_dmg
    )
    score += _POWER_WEIGHTS["structure_damage"] * _percentile_in(
        baseline.structure_damage, avg_structure_dmg
    )
    score += _POWER_WEIGHTS["healing"] * _percentile_in(
        baseline.healing, avg_healing
    )
    score += _POWER_WEIGHTS["damage_soaked"] * _percentile_in(
        baseline.damage_soaked, avg_dmg_soaked
    )
    score += _POWER_WEIGHTS["experience"] * _percentile_in(
        baseline.xp, avg_xp
    )
    score += _POWER_WEIGHTS["cc"] * _percentile_in(baseline.cc, avg_cc)
    # Deaths: lower is better. Percentile in raw deaths gives "how many
    # of the population die >= as much as me" if we flip the sign.
    score += _POWER_WEIGHTS["deaths_inverse"] * (
        1.0 - _percentile_in(baseline.deaths, avg_d)
    )
    return score * 100.0


def _score_population(
    rows: list[PlayerRankRow], baseline: PowerBaseline
) -> list[PlayerRankRow]:
    """Attach a power score to each row using the shared global baseline."""
    out: list[PlayerRankRow] = []
    for r in rows:
        s = power_score(
            baseline=baseline,
            win_rate=r.win_rate,
            avg_k=r.avg_k, avg_d=r.avg_d, avg_a=r.avg_a,
            avg_hero_dmg=r.avg_hero_dmg,
            avg_siege_dmg=r.avg_siege_dmg,
            avg_structure_dmg=r.avg_structure_dmg,
            avg_healing=r.avg_healing,
            avg_dmg_soaked=r.avg_dmg_soaked,
            avg_xp=r.avg_xp,
            avg_cc=r.avg_cc,
        )
        out.append(PlayerRankRow(**{**r.__dict__, "power": s}))
    return out


def _sort_and_rerank(
    scored: list[PlayerRankRow],
    *,
    direction: str,
    sort_mode: str,
    limit: int,
) -> list[PlayerRankRow]:
    """Sort ``scored`` and slice to ``limit``, then renumber ranks.

    ``sort_mode`` picks the primary key:
    * ``SORT_WLB``   — Wilson 95% LB, then raw WR, then games, then KDA.
    * ``SORT_POWER`` — composite power score, with WLB as tiebreaker.

    ``direction`` is ``"asc"`` for the worst-of boards and ``"desc"``
    for the best-of boards. Tiebreakers flip with the direction so a
    "worst" board breaks ties on the same direction.
    """
    if sort_mode == SORT_POWER:
        if direction == "asc":
            key = lambda p: (p.power, p.wilson_lb, p.win_rate, -p.games)
        else:
            key = lambda p: (-p.power, -p.wilson_lb, -p.win_rate, -p.games)
    else:
        if direction == "asc":
            key = lambda p: (p.wilson_lb, p.win_rate, -p.games, p.kda)
        else:
            key = lambda p: (-p.wilson_lb, -p.win_rate, -p.games, -p.kda)
    scored = sorted(scored, key=key)
    return [
        PlayerRankRow(**{**p.__dict__, "rank": i + 1})
        for i, p in enumerate(scored[:limit])
    ]


def compute_board(
    store: Store,
    board: str,
    *,
    min_games: int = 5,
    limit: int = 10,
    sort_mode: str = SORT_WLB,
    baseline: PowerBaseline | None = None,
) -> list[PlayerRankRow]:
    """Compute one of :data:`ALL_BOARDS`.

    Both teammate boards share an underlying SQL query (filtered by
    ``team = our_team``) and just sort opposite directions; the
    opponent boards do the same with ``team != our_team``. We keep
    them as separate calls so the caller can render only what the
    dropdown currently asks for, with no wasted aggregation.

    ``sort_mode`` controls the primary sort key — see
    :func:`_sort_and_rerank`. The composite power score is always
    computed (it's cheap) using either the provided ``baseline`` or
    a freshly-built one, so switching modes in the UI doesn't
    require re-querying the DB. Callers that score multiple boards
    in one shot should pass a shared baseline.
    """
    if board not in ALL_BOARDS:
        raise ValueError(f"unknown board: {board!r}")
    if sort_mode not in ALL_SORTS:
        raise ValueError(f"unknown sort_mode: {sort_mode!r}")

    squad = tuple(store.squad_handles())
    if not squad:
        return []

    side = "teammate" if "teammate" in board else "opponent"
    direction = "asc" if board.startswith("worst_") else "desc"

    rows = store.player_rankings_vs_squad(
        squad,
        side=side,
        min_games=min_games,
        # Pull a generous slice so the post-sort still has enough
        # signal even when the SQL ORDER BY (games desc) doesn't
        # match either direction we care about.
        limit=max(limit * 6, 200),
    )
    if not rows:
        return []
    scored = [_row_to_rank(r, rank=0) for r in rows]
    if baseline is None:
        baseline = build_power_baseline(store)
    scored = _score_population(scored, baseline)
    return _sort_and_rerank(
        scored,
        direction=direction,
        sort_mode=sort_mode,
        limit=limit,
    )


def highlight_indices(
    store: Store,
    *,
    top_n: int = 30,
    min_games: int = 5,
) -> dict[str, dict[str, PlayerRankRow]]:
    """Per-board ``{toon_handle: PlayerRankRow}`` dicts for fast lookup.

    The BP popup uses two of these (worst teammate / best opponent) to
    flag player cards. Returning all four keeps the API symmetric
    even though the popup highlight is currently only on the two
    "danger" boards.
    """
    return {
        board: {p.toon_handle: p for p in compute_board(
            store, board, min_games=min_games, limit=top_n,
        ) if p.toon_handle}
        for board in ALL_BOARDS
    }
