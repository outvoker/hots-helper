"""Health, DB stats, and reference-data endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

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
