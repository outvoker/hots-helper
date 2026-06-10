"""Tests for Store.match_detail — full roster of one replay."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_match_detail_returns_replay_and_full_roster(seeded_store):
    # Match 0 was seeded first → replay id 1 (autoincrement).
    replay_id = seeded_store.conn.execute(
        "SELECT id FROM replays WHERE match_key = 'match0000'"
    ).fetchone()["id"]

    replay, players = seeded_store.match_detail(replay_id)

    assert replay is not None
    assert replay["map_name"] == "白银城"
    assert replay["mode"] == "Storm League"
    assert len(players) == 10
    # Ordered by team then slot.
    teams = [p["team"] for p in players]
    assert teams == sorted(teams)
    assert players[0]["team"] == 0
    assert players[-1]["team"] == 1


def test_match_detail_roster_carries_stats(seeded_store):
    replay_id = seeded_store.conn.execute(
        "SELECT id FROM replays WHERE match_key = 'match0000'"
    ).fetchone()["id"]
    _replay, players = seeded_store.match_detail(replay_id)

    ali = next(p for p in players if p["display_name"] == "阿离")
    assert ali["hero"] == "李敏"
    assert ali["hero_damage"] == 80_000
    assert ali["result"] == 1


def test_match_detail_unknown_id_returns_none_and_empty(seeded_store):
    replay, players = seeded_store.match_detail(999_999)
    assert replay is None
    assert players == []
