"""Cross-game player power rankings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ...db import Store
from ...player_rank import compute_player_rankings
from .. import serialize
from ..deps import get_squad, get_store

router = APIRouter(prefix="/api/rankings", tags=["rankings"])


@router.get("/players")
def player_rankings(
    request: Request,
    min_games: int = Query(5, ge=1),
    hero: str | None = Query(None),
    mode: str | None = Query(None),
    squad: tuple[str, ...] | None = Depends(get_squad),
    store: Store = Depends(get_store),
) -> list[dict]:
    """Every player with enough games, ranked by the composite
    combat-power score.

    Reuses the provider's cached :class:`PowerBaseline` so the
    whole-table scan happens once per refresh, not per request. The
    board population is everyone with ``>= min_games`` in ``mode`` (a
    single mode name, default Storm League); ``squad`` only flags
    ``is_squad`` rows for highlighting and never changes membership.
    """
    baseline = request.app.state.provider.baseline()
    mode_filter = (mode,) if mode else None
    rows = compute_player_rankings(
        store,
        min_games=min_games,
        hero=hero,
        baseline=baseline,
        squad=squad,
        mode_filter=mode_filter,
    )
    return [serialize.player_rank_row(r) for r in rows]
