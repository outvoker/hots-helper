"""Match records: filtered list + single-match detail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...db import Store
from .. import serialize
from ..deps import get_store

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("")
def list_matches(
    map_name: str | None = Query(None, alias="map"),
    mode: str | None = Query("Storm League"),
    player: str | None = Query(None),
    result: int | None = Query(None, ge=0, le=1),
    since: str | None = Query(None, alias="from"),
    until: str | None = Query(None, alias="to"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    store: Store = Depends(get_store),
) -> dict:
    """Paginated match list, newest first.

    ``result`` (1 win / 0 loss) is only meaningful with a ``player``
    filter — without it a match has no single outcome, so we reject the
    combination with 422.
    """
    if result is not None and not player:
        raise HTTPException(
            status_code=422,
            detail="`result` filter requires a `player` filter",
        )
    # `mode` can be cleared by passing an empty string → include all modes.
    mode_filter = mode or None
    rows, total = store.list_matches(
        map_name=map_name,
        mode=mode_filter,
        player=player,
        result=result,
        since_iso=since,
        until_iso=until,
        limit=limit,
        offset=offset,
    )
    roster = store.match_roster_brief([int(r["id"]) for r in rows])
    by_replay: dict[int, list] = {}
    for r in roster:
        by_replay.setdefault(int(r["replay_id"]), []).append(r)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "matches": [
            serialize.match_list_row(r, by_replay.get(int(r["id"]), [])) for r in rows
        ],
    }


@router.get("/{replay_id}")
def match_detail(replay_id: int, store: Store = Depends(get_store)) -> dict:
    replay, players = store.match_detail(replay_id)
    if replay is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return serialize.match_detail(replay, players)
