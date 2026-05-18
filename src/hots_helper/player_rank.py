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


# Weights for the "combat power" score. Tuned by feel: KDA + win rate
# carry the most weight (they're the cleanest signals of "this player
# does the right thing in fights"); damage / soak / xp / cc fill in
# the rest. Each component is normalised to 0..1 against the board's
# own population (``_score_population``) so the weights are about
# *relative importance*, not absolute units.
#
# Self-healing is excluded — it's mostly inflated by self-sustain
# heroes' baseline regen and doesn't track player skill well.
_POWER_WEIGHTS: dict[str, float] = {
    "win_rate":          0.30,
    "wilson_lb":         0.10,
    "kda":               0.20,
    "hero_damage":       0.10,
    "siege_damage":      0.04,
    "structure_damage":  0.03,
    "healing":           0.07,
    "damage_soaked":     0.05,
    "experience":        0.06,
    "cc":                0.03,
    "deaths_inverse":    0.02,  # fewer deaths = higher
}


def _percentile_norm(values: list[float], v: float) -> float:
    """Population-relative normalisation in 0..1.

    Returns the fraction of the population that ``v`` ranks at-or-
    below — equivalent to a percentile rank. We use this instead of
    naive min-max scaling because the metrics are heavy-tailed
    (top-1% players post 5x median hero damage), so min-max gives the
    bottom 90% nearly-zero scores and a straight line of 0.95+ at the
    tail. Percentiles compress that tail without losing ordering.
    """
    if not values:
        return 0.0
    n = len(values)
    # Count of values ≤ v.
    le = sum(1 for x in values if x <= v)
    return le / n


def _score_population(rows: list[PlayerRankRow]) -> list[PlayerRankRow]:
    """Compute and attach the composite power score to each row."""
    if not rows:
        return rows

    # Pre-extract each metric so we can pass through the percentile fn.
    pop = {
        "win_rate":         [r.win_rate for r in rows],
        "wilson_lb":        [r.wilson_lb for r in rows],
        "kda":              [r.kda for r in rows],
        "hero_damage":      [r.avg_hero_dmg for r in rows],
        "siege_damage":     [r.avg_siege_dmg for r in rows],
        "structure_damage": [r.avg_structure_dmg for r in rows],
        "healing":          [r.avg_healing for r in rows],
        "damage_soaked":    [r.avg_dmg_soaked for r in rows],
        "experience":       [r.avg_xp for r in rows],
        "cc":               [r.avg_cc for r in rows],
        # Deaths is the only metric where lower is better — we negate
        # so the percentile gives "fewer-deaths-is-better" as 1.0.
        "deaths_inverse":   [-r.avg_d for r in rows],
    }

    out: list[PlayerRankRow] = []
    for r in rows:
        score = 0.0
        for key, weight in _POWER_WEIGHTS.items():
            value = {
                "win_rate":         r.win_rate,
                "wilson_lb":        r.wilson_lb,
                "kda":              r.kda,
                "hero_damage":      r.avg_hero_dmg,
                "siege_damage":     r.avg_siege_dmg,
                "structure_damage": r.avg_structure_dmg,
                "healing":          r.avg_healing,
                "damage_soaked":    r.avg_dmg_soaked,
                "experience":       r.avg_xp,
                "cc":               r.avg_cc,
                "deaths_inverse":   -r.avg_d,
            }[key]
            score += weight * _percentile_norm(pop[key], value)
        # Scale to 0..100 for display.
        out.append(PlayerRankRow(**{**r.__dict__, "power": score * 100.0}))
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
) -> list[PlayerRankRow]:
    """Compute one of :data:`ALL_BOARDS`.

    Both teammate boards share an underlying SQL query (filtered by
    ``team = our_team``) and just sort opposite directions; the
    opponent boards do the same with ``team != our_team``. We keep
    them as separate calls so the caller can render only what the
    dropdown currently asks for, with no wasted aggregation.

    ``sort_mode`` controls the primary sort key — see
    :func:`_sort_and_rerank`. The composite power score is always
    computed (it's cheap), so switching modes in the UI doesn't
    require re-querying the DB.
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
    scored = _score_population(scored)
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
