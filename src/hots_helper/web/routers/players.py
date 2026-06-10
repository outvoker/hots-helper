"""Player search, per-player profile, and per-player match history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...db import Store
from ...lookup import lookup_players
from .. import serialize
from ..deps import get_store

router = APIRouter(prefix="/api/players", tags=["players"])


@router.get("")
def search_players(
    name: str = Query(..., min_length=1),
    map_name: str | None = Query(None, alias="map"),
    store: Store = Depends(get_store),
) -> list[dict]:
    """Look a player up by display name (exact → case-insensitive →
    fuzzy). Returns one summary per matching handle."""
    results = lookup_players(store, [name], map_name=map_name)
    summaries = results.get(name, [])
    return [serialize.player_summary(s) for s in summaries]


@router.get("/{handle}")
def player_profile(
    handle: str,
    map_name: str | None = Query(None, alias="map"),
    store: Store = Depends(get_store),
) -> dict:
    """Full profile for a known toon_handle.

    Resolves the handle's display name, then reuses the same lookup
    pipeline so the response shape matches search results.
    """
    row = store.conn.execute(
        "SELECT display_name FROM players WHERE toon_handle = ?", (handle,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown handle {handle!r}")
    results = lookup_players(store, [row["display_name"]], map_name=map_name)
    for summaries in results.values():
        for s in summaries:
            if s.toon_handle == handle:
                return serialize.player_summary(s)
    raise HTTPException(status_code=404, detail=f"Unknown handle {handle!r}")


@router.get("/{handle}/matches")
def player_matches(
    handle: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    mode: str | None = Query("Storm League"),
    store: Store = Depends(get_store),
) -> dict:
    """This player's matches, newest first, with pagination metadata."""
    rows, total = store.list_matches(
        player=handle, mode=mode, limit=limit, offset=offset
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
