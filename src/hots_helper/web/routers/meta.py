"""Health, DB stats, and reference-data endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ... import heroes as heroes_data
from ... import maps as maps_data
from ...db import Store
from ...hero_roles import HERO_ROLE
from ..deps import get_store

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health(store: Store = Depends(get_store)) -> dict:
    """Liveness + readiness. Always 200 once the app has booted."""
    return {"status": "ok", "replays": store.count_replays()}


@router.get("/stats")
def stats(store: Store = Depends(get_store)) -> dict:
    """Database summary: totals + per-mode replay counts."""
    by_mode = {
        r["mode"]: int(r["n"])
        for r in store.conn.execute(
            "SELECT mode, COUNT(*) AS n FROM replays GROUP BY mode ORDER BY n DESC"
        ).fetchall()
    }
    return {
        "replays": store.count_replays(),
        "players": store.count_players(),
        "by_mode": by_mode,
    }


@router.get("/reference")
def reference() -> dict:
    """Static reference data the frontend needs for dropdowns/labels."""
    return {
        "storm_league_maps": list(maps_data.STORM_LEAGUE_MAPS),
        "aram_maps": list(maps_data.ARAM_MAPS),
        "heroes": sorted(heroes_data.HERO_NAMES_ZH),
        "hero_roles": dict(HERO_ROLE),
    }


@router.get("/squad/candidates")
def squad_candidates(
    min_games: int = Query(10, ge=1),
    limit: int = Query(60, ge=1, le=200),
    store: Store = Depends(get_store),
) -> dict:
    """Frequent players for the squad-picker UI, most games first.

    The roster itself lives client-side (per browser); this endpoint
    only supplies the candidate list to choose from. ``suggested`` is the
    server's heuristic guess so a first-time user can one-click accept it.
    """
    candidates = store.squad_candidates(min_games=min_games, limit=limit)
    suggested = sorted(store.squad_handles())
    return {"candidates": candidates, "suggested": suggested}
