"""Ban / pick advisor for the draft phase."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...bp import (
    profile_opponents,
    recommend_bans,
    recommend_map_strong_bans,
    recommend_picks,
)
from ...db import Store
from ...player_rank import compute_player_rankings
from .. import serialize
from ..deps import get_store

router = APIRouter(prefix="/api/bp", tags=["bp"])


class ProfileRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    map_name: str | None = Field(None, alias="map")


class BansRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    already_banned: list[str] = Field(default_factory=list)
    mode: str = "relative"


class PicksRequest(BaseModel):
    map_name: str = Field(..., alias="map")
    exclude_heroes: list[str] = Field(default_factory=list)


class MapBansRequest(BaseModel):
    map_name: str = Field(..., alias="map")


@router.post("/profile")
def profile(
    body: ProfileRequest,
    request: Request,
    store: Store = Depends(get_store),
) -> list[dict]:
    """Per-opponent threat breakdown, enriched with power rank + the
    ally/enemy split against the squad."""
    squad = tuple(store.squad_handles())
    baseline = request.app.state.provider.baseline()
    ranked = compute_player_rankings(store, min_games=1, baseline=baseline)
    rank_index = {r.toon_handle: r for r in ranked}
    profiles = profile_opponents(
        store,
        [n for n in body.names if n],
        rank_index=rank_index,
        rank_total=len(ranked),
        squad_handles=squad,
    )
    return [serialize.opponent_profile(p) for p in profiles]


@router.post("/bans")
def bans(body: BansRequest, store: Store = Depends(get_store)) -> list[dict]:
    recs = recommend_bans(
        store,
        [n for n in body.names if n],
        already_banned=set(body.already_banned),
        mode=body.mode,
    )
    return [serialize.ban_candidate(c) for c in recs]


@router.post("/picks")
def picks(body: PicksRequest, store: Store = Depends(get_store)) -> list[dict]:
    recs = recommend_picks(
        store, body.map_name, exclude_heroes=set(body.exclude_heroes)
    )
    return [serialize.pick_candidate(c) for c in recs]


@router.post("/map-bans")
def map_bans(body: MapBansRequest, store: Store = Depends(get_store)) -> list[dict]:
    squad = list(store.squad_handles())
    recs = recommend_map_strong_bans(store, body.map_name, squad)
    return [serialize.map_tier_ban(b) for b in recs]
