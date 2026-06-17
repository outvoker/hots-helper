"""Hero rankings + per-hero deep dive."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...db import Store
from ...lookup import hero_report as build_hero_report
from ...talent_build import build_talent_recommendation
from .. import serialize
from ..deps import get_store

router = APIRouter(prefix="/api/heroes", tags=["heroes"])


@router.get("")
def list_heroes(
    map_name: str | None = Query(None, alias="map"),
    store: Store = Depends(get_store),
) -> list[dict]:
    """All heroes with games/winrate/Wilson lower bound, optionally
    scoped to one map."""
    rows = store.hero_aggregate_stats(map_name=map_name)
    return [serialize.hero_aggregate_row(r) for r in rows if int(r["games"] or 0)]


@router.get("/{hero}")
def hero_detail(
    hero: str,
    map_name: str | None = Query(None, alias="map"),
    store: Store = Depends(get_store),
) -> dict:
    report = build_hero_report(store, hero, map_name=map_name)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No data for hero {hero!r}")
    return serialize.hero_report(report)


@router.get("/{hero}/talents")
def hero_talents(
    hero: str,
    mode: str = Query("standard", description="Mode bucket: 'standard' or 'aram'"),
    store: Store = Depends(get_store),
) -> dict:
    """Winrate-based recommended talent build for a hero, in one mode
    bucket (standard = Storm League + Quick Match; aram = ARAM)."""
    build = build_talent_recommendation(store, hero, mode_group=mode)
    return serialize.talent_build(build)
