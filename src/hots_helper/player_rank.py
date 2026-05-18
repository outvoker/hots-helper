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


def _sort_and_rerank(
    scored: list[PlayerRankRow],
    *,
    direction: str,
    limit: int,
) -> list[PlayerRankRow]:
    """Sort ``scored`` by Wilson LB in the requested direction, slice
    to ``limit``, and rebuild rank numbers."""
    if direction == "asc":
        scored = sorted(
            scored,
            key=lambda p: (p.wilson_lb, p.win_rate, -p.games, p.kda),
        )
    else:
        scored = sorted(
            scored,
            key=lambda p: (-p.wilson_lb, -p.win_rate, -p.games, -p.kda),
        )
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
) -> list[PlayerRankRow]:
    """Compute one of :data:`ALL_BOARDS`.

    Both teammate boards share an underlying SQL query (filtered by
    ``team = our_team``) and just sort opposite directions; the
    opponent boards do the same with ``team != our_team``. We keep
    them as separate calls so the caller can render only what the
    dropdown currently asks for, with no wasted aggregation.
    """
    if board not in ALL_BOARDS:
        raise ValueError(f"unknown board: {board!r}")

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
    return _sort_and_rerank(scored, direction=direction, limit=limit)


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
