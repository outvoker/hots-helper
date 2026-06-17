"""Winrate-based talent build recommendation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hots_helper.db import Store
from hots_helper.parser.replay import PlayerMatch, Replay
from hots_helper.talent_build import (
    MODE_GROUPS,
    build_talent_recommendation,
    normalize_mode_group,
)

WIN, LOSS = 1, 2
_T = datetime(2026, 5, 1, 20, 0, 0, tzinfo=timezone.utc)


def _pm(slot, hero, team, result, talents):
    return PlayerMatch(
        slot=slot, name=f"p{slot}", toon_handle=f"1-Hero-1-p{slot}",
        hero=hero, hero_id=hero[:4], skin="", banner="", team=team,
        result=result, kills=5, deaths=3, assists=8, takedowns=13,
        solo_kills=1, level=20, hero_damage=60_000, siege_damage=20_000,
        structure_damage=2_000, healing=0, self_healing=0,
        damage_taken=15_000, damage_soaked=0, experience_contribution=30_000,
        time_cc_enemy_heroes=2, talents=talents, awards=[],
    )


def _replay(idx, mode, players, winner=0):
    return Replay(
        file_path=f"/tmp/r{idx}.StormReplay", file_hash=f"h{idx}",
        match_key=f"m{idx}", random_seed=idx, map_name="白银城",
        mode=mode, build=90000, protocol_build=90000,
        played_at=_T, duration_seconds=1200, winner_team=winner,
        bans=[], bans_team0=[], bans_team1=[], players=players,
    )


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "talents.db")
    fillers = lambda: [_pm(i, "李敏", 0 if i < 5 else 1, WIN if i < 5 else LOSS, ["x"])
                       for i in range(1, 10)]
    # Storm League: hero takes T1 talent "A" and wins, "B" and loses.
    for i in range(6):
        s.upsert_replay(_replay(100 + i, "Storm League",
                                [_pm(0, "源氏", 0, WIN, ["源A"])] + fillers()))
    for i in range(4):
        s.upsert_replay(_replay(200 + i, "Storm League",
                                [_pm(0, "源氏", 1, LOSS, ["源B"])] + fillers(), winner=0))
    # Quick Match: pooled with Storm League under "standard".
    for i in range(3):
        s.upsert_replay(_replay(300 + i, "Quick Match",
                                [_pm(0, "源氏", 0, WIN, ["源A"])] + fillers()))
    # ARAM: a *different* talent "C" dominates.
    for i in range(5):
        s.upsert_replay(_replay(400 + i, "ARAM",
                                [_pm(0, "源氏", 0, WIN, ["源C"])] + fillers()))
    return s


def test_mode_groups_pool_standard_and_isolate_aram():
    assert MODE_GROUPS["standard"] == ("Storm League", "Quick Match")
    assert MODE_GROUPS["aram"] == ("ARAM",)


def test_normalize_mode_group_defaults_safely():
    assert normalize_mode_group(None) == "standard"
    assert normalize_mode_group("nonsense") == "standard"
    assert normalize_mode_group("aram") == "aram"


def test_standard_pools_sl_and_qm(store):
    build = build_talent_recommendation(store, "源氏", mode_group="standard")
    # 6 SL wins (源A) + 4 SL losses (源B) + 3 QM wins (源A) = 13 standard games.
    assert build.total_games == 13
    t1 = build.tiers[0]
    # 源A appears 9 times (6 SL + 3 QM wins), 源B 4 times — 源A recommended.
    assert t1.recommended.talent == "源A"
    assert t1.recommended.games == 9


def test_aram_isolated_and_distinct(store):
    build = build_talent_recommendation(store, "源氏", mode_group="aram")
    assert build.total_games == 5
    t1 = build.tiers[0]
    assert t1.recommended.talent == "源C"  # ARAM-only talent


def test_recommended_is_highest_wilson_lb(store):
    # Standard T1: 源A (9 games, all wins) should beat 源B (4 games, all losses).
    build = build_talent_recommendation(store, "源氏", mode_group="standard")
    t1 = build.tiers[0]
    assert t1.recommended.talent == "源A"
    assert t1.recommended.wilson_lb >= max(
        c.wilson_lb for c in t1.choices if c.talent != "源A"
    )


def test_unknown_hero_returns_empty(store):
    build = build_talent_recommendation(store, "不存在", mode_group="standard")
    assert build.tiers == []
    assert build.total_games == 0
