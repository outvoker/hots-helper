"""Cross-game player rankings — "kingmakers" (worst teammates ever) and
"boogeymen" (strongest opponents ever).

Drives two things:
* the standalone ranking dialog (a "Hall of Shame / Hall of Fame")
* a fast handle-set lookup the BP popup uses to highlight player cards
  whose handle shows up near the top of either board

Ranking philosophy:
* Score by Wilson 95% lower bound on win-rate (over min-games threshold)
  rather than raw win-rate, so a 3-game player on a hot streak doesn't
  outrank a real signal.
* The "worst teammate" board orders ascending by Wilson; the "strongest
  opponent" board orders descending. Both boards exclude the squad's
  own handles (heuristically detected by play frequency).
* Tiebreak on KDA / hero damage so two equally-bad players sort by how
  much they actually hurt your team.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import Store
from .stats import wilson_lower_bound


@dataclass(frozen=True)
class PlayerRankRow:
    """One row of either ranking board."""
    rank: int                # 1-indexed
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
    avg_healing: float
    avg_xp: float
    last_seen_at: str

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
        avg_healing=float(row["avg_healing"] or 0.0),
        avg_xp=float(row["avg_xp"] or 0.0),
        last_seen_at=row["last_seen_at"] or "",
    )


def compute_rankings(
    store: Store,
    *,
    min_games: int = 5,
    limit: int = 100,
    include_squad: bool = False,
) -> tuple[list[PlayerRankRow], list[PlayerRankRow]]:
    """Return ``(worst_teammates, best_opponents)`` lists.

    Both lists are derived from the *same* per-player aggregate; we just
    sort one ascending and one descending. ``min_games`` and ``limit``
    apply to each list independently. ``include_squad=True`` keeps our
    own handles in the listing — the dialog exposes a checkbox for this
    so a user can sanity-check that the squad isn't accidentally on the
    boogeyman board.
    """
    exclude: tuple[str, ...] = ()
    if not include_squad:
        exclude = tuple(store.squad_handles())

    rows = store.player_rankings(
        min_games=min_games,
        # Pull plenty so we can sort and slice afterward; SQL ORDER BY
        # is by games desc which doesn't match either board's order.
        limit=max(limit * 4, 200),
        exclude_handles=exclude,
    )
    if not rows:
        return [], []

    # Score everyone once.
    scored = [(_row_to_rank(r, rank=0)) for r in rows]

    # Worst teammates: lowest Wilson LB first. Tiebreak by raw win-rate
    # asc, then by games desc (more games = more confidence in the
    # bad-ness), then by KDA asc (worse KDA tied = worse).
    worst = sorted(
        scored,
        key=lambda p: (p.wilson_lb, p.win_rate, -p.games, p.kda),
    )[:limit]
    worst = [
        PlayerRankRow(**{**p.__dict__, "rank": i + 1})
        for i, p in enumerate(worst)
    ]

    # Best opponents: highest Wilson LB first. Tiebreak by raw win-rate
    # desc, then games desc, then KDA desc.
    best = sorted(
        scored,
        key=lambda p: (-p.wilson_lb, -p.win_rate, -p.games, -p.kda),
    )[:limit]
    best = [
        PlayerRankRow(**{**p.__dict__, "rank": i + 1})
        for i, p in enumerate(best)
    ]

    return worst, best


def highlight_handles(
    store: Store,
    *,
    top_n: int = 20,
    min_games: int = 5,
) -> tuple[set[str], set[str]]:
    """Handle sets used by the BP popup to flag cards.

    Returns ``(worst_handles, best_handles)``. Both sets are subsets of
    the boards from :func:`compute_rankings` — small, so a card refresh
    can do an O(1) membership test per slot.
    """
    worst, best = compute_rankings(store, min_games=min_games, limit=top_n)
    return (
        {p.toon_handle for p in worst if p.toon_handle},
        {p.toon_handle for p in best if p.toon_handle},
    )
