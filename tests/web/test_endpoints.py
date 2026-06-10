"""Smoke tests: every endpoint returns the right shape on seeded data."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["replays"] == 4


def test_stats(client):
    r = client.get("/api/stats")
    body = r.json()
    assert r.status_code == 200
    assert body["replays"] == 4
    assert body["by_mode"]["Storm League"] == 3
    assert body["by_mode"]["ARAM"] == 1


def test_reference(client):
    body = client.get("/api/reference").json()
    assert "巨龙镇" in body["storm_league_maps"]
    assert isinstance(body["heroes"], list)
    assert isinstance(body["hero_roles"], dict)


def test_heroes_list(client):
    body = client.get("/api/heroes").json()
    assert len(body) > 0
    limin = next(h for h in body if h["hero"] == "李敏")
    assert "winrate" in limin and "wilson_lb" in limin


def test_heroes_filter_by_map(client):
    body = client.get("/api/heroes", params={"map": "白银城"}).json()
    assert all(h["games"] > 0 for h in body)


def test_hero_detail(client):
    r = client.get("/api/heroes/李敏")
    assert r.status_code == 200
    body = r.json()
    assert body["hero"] == "李敏"
    assert "winrate" in body
    assert "talents_by_tier" in body


def test_hero_detail_unknown_404(client):
    assert client.get("/api/heroes/不存在的英雄").status_code == 404


def test_player_search(client):
    body = client.get("/api/players", params={"name": "阿离"}).json()
    assert len(body) == 1
    assert body[0]["display_name"] == "阿离"
    assert "winrate" in body[0]
    assert body[0]["overall_kda"].keys() >= {"k", "d", "a"}


def test_player_profile_and_matches(client):
    handle = "1-Hero-1-阿离"
    prof = client.get(f"/api/players/{handle}")
    assert prof.status_code == 200
    matches = client.get(f"/api/players/{handle}/matches").json()
    assert matches["total"] == 3
    assert len(matches["matches"]) == 3


def test_player_profile_unknown_404(client):
    assert client.get("/api/players/nope").status_code == 404


def test_player_rankings(client):
    body = client.get("/api/rankings/players", params={"min_games": 1}).json()
    assert isinstance(body, list)
    if body:
        assert "power" in body[0] and "kda" in body[0]


def test_matches_list(client):
    body = client.get("/api/matches").json()
    assert body["total"] == 3
    first = body["matches"][0]
    assert len(first["team0"]) == 5
    assert len(first["team1"]) == 5
    assert isinstance(first["bans_team0"], list)


def test_matches_result_requires_player_422(client):
    assert client.get("/api/matches", params={"result": 1}).status_code == 422


def test_matches_filter_player_result(client):
    body = client.get("/api/matches", params={"player": "阿离", "result": 1}).json()
    assert body["total"] == 2


def test_match_detail(client):
    listing = client.get("/api/matches").json()
    replay_id = listing["matches"][0]["replay_id"]
    detail = client.get(f"/api/matches/{replay_id}").json()
    assert len(detail["players"]) == 10
    assert detail["players"][0]["team"] == 0


def test_match_detail_404(client):
    assert client.get("/api/matches/999999").status_code == 404


def test_bp_profile(client):
    r = client.post("/api/bp/profile", json={"names": ["敌人A"], "map": "白银城"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_bp_bans(client):
    r = client.post("/api/bp/bans", json={"names": ["敌人A", "敌人B"]})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_bp_picks(client):
    r = client.post("/api/bp/picks", json={"map": "白银城"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_bp_map_bans(client):
    r = client.post("/api/bp/map-bans", json={"map": "白银城"})
    assert r.status_code == 200


def test_weekly(client):
    # Seeded matches are dated 2026-05; a 7-day window from "now" is empty,
    # but the endpoint must still return a well-formed report.
    body = client.get("/api/weekly", params={"days": 3650}).json()
    assert "overview" in body
    assert "awards" in body
    assert "brief" in body
