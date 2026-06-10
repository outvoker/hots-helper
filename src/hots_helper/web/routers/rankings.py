"""Cross-game player power rankings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ...db import Store
from ...player_rank import compute_player_rankings
from .. import serialize
from ..deps import get_store

router = APIRouter(prefix="/api/rankings", tags=["rankings"])


@router.get("/players")
def player_rankings(
    request: Request,
    min_games: int = Query(5, ge=1),
    hero: str | None = Query(None),
    store: Store = Depends(get_store),
) -> list[dict]:
    """Every player who's shared a match with the squad, ranked by the
    composite combat-power score.

    Reuses the provider's cached :class:`PowerBaseline` so the
    whole-table scan happens once per refresh, not per request.
    """
    baseline = request.app.state.provider.baseline()
    rows = compute_player_rankings(
        store, min_games=min_games, hero=hero, baseline=baseline
    )
    return [serialize.player_rank_row(r) for r in rows]
