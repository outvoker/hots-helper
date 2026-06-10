"""Shared pytest fixtures for the test suite.

The ``seeded_store`` fixture builds a small but realistic
:class:`hots_helper.db.Store` by running the *real* ingest path
(``upsert_replay``) over a handful of synthetic replays. Using the real
insert path means the store queries under test exercise exactly the
schema and row shapes production data has.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hots_helper.db import Store
from hots_helper.parser.replay import PlayerMatch, Replay

# Result convention mirrors the parser: 1 = win, 2 = loss.
WIN = 1
LOSS = 2

_BASE_TIME = datetime(2026, 5, 1, 20, 0, 0, tzinfo=timezone.utc)


def _player(
    slot: int,
    name: str,
    hero: str,
    team: int,
    result: int,
    *,
    kills: int = 5,
    deaths: int = 3,
    assists: int = 8,
    hero_damage: int = 60_000,
    healing: int = 0,
    talents: list[str] | None = None,
) -> PlayerMatch:
    """Build a PlayerMatch with sensible defaults so tests only specify
    the fields they care about."""
    return PlayerMatch(
        slot=slot,
        name=name,
        toon_handle=f"1-Hero-1-{name}",
        hero=hero,
        hero_id=hero[:4],
        skin="",
        banner="",
        team=team,
        result=result,
        kills=kills,
        deaths=deaths,
        assists=assists,
        takedowns=kills + assists,
        solo_kills=1,
        level=20,
        hero_damage=hero_damage,
        siege_damage=20_000,
        structure_damage=2_000,
        healing=healing,
        self_healing=500,
        damage_taken=15_000,
        damage_soaked=0,
        experience_contribution=30_000,
        time_cc_enemy_heroes=2,
        talents=talents or ["T1", "T4", "T7"],
        awards=[],
    )


def _replay(
    idx: int,
    *,
    map_name: str,
    mode: str,
    played_at: datetime,
    players: list[PlayerMatch],
    winner_team: int = 0,
    bans_team0: list[str] | None = None,
    bans_team1: list[str] | None = None,
) -> Replay:
    return Replay(
        file_path=f"/tmp/replay_{idx}.StormReplay",
        file_hash=f"hash{idx:04d}",
        match_key=f"match{idx:04d}",
        random_seed=1000 + idx,
        map_name=map_name,
        mode=mode,
        build=90000,
        protocol_build=90000,
        played_at=played_at,
        duration_seconds=1200,
        winner_team=winner_team,
        bans=[],
        bans_team0=bans_team0 or ["瓦里安", "缝合怪"],
        bans_team1=bans_team1 or ["李敏"],
        players=players,
    )


def _build_replays() -> list[Replay]:
    """Three Storm League matches + one ARAM match.

    Squad members ("阿离", "老狼") appear in every Storm League match so
    the squad-detection heuristic and per-player history queries have
    something to chew on.
    """
    replays: list[Replay] = []

    # Match 0 — 白银城, squad wins (team 0).
    replays.append(
        _replay(
            0,
            map_name="白银城",
            mode="Storm League",
            played_at=_BASE_TIME,
            winner_team=0,
            players=[
                _player(0, "阿离", "李敏", 0, WIN, hero_damage=80_000),
                _player(1, "老狼", "玛法里奥", 0, WIN, healing=45_000),
                _player(2, "队友A", "缝合怪", 0, WIN),
                _player(3, "队友B", "瓦里安", 0, WIN),
                _player(4, "队友C", "源氏", 0, WIN),
                _player(5, "敌人A", "古尔丹", 1, LOSS),
                _player(6, "敌人B", "乌瑟尔", 1, LOSS, healing=40_000),
                _player(7, "敌人C", "迪亚波罗", 1, LOSS),
                _player(8, "敌人D", "凯尔萨斯", 1, LOSS),
                _player(9, "敌人E", "缝合怪", 1, LOSS),
            ],
        )
    )

    # Match 1 — 巨龙镇, squad loses (team 1 wins).
    replays.append(
        _replay(
            1,
            map_name="巨龙镇",
            mode="Storm League",
            played_at=_BASE_TIME + timedelta(hours=1),
            winner_team=1,
            players=[
                _player(0, "阿离", "源氏", 0, LOSS),
                _player(1, "老狼", "乌瑟尔", 0, LOSS, healing=38_000),
                _player(2, "队友A", "缝合怪", 0, LOSS),
                _player(3, "队友B", "瓦里安", 0, LOSS),
                _player(4, "队友C", "李敏", 0, LOSS),
                _player(5, "敌人A", "古尔丹", 1, WIN, hero_damage=90_000),
                _player(6, "敌人B", "乌瑟尔", 1, WIN, healing=50_000),
                _player(7, "敌人C", "迪亚波罗", 1, WIN),
                _player(8, "敌人D", "凯尔萨斯", 1, WIN),
                _player(9, "敌人E", "源氏", 1, WIN),
            ],
        )
    )

    # Match 2 — 白银城 again, squad wins.
    replays.append(
        _replay(
            2,
            map_name="白银城",
            mode="Storm League",
            played_at=_BASE_TIME + timedelta(days=1),
            winner_team=0,
            players=[
                _player(0, "阿离", "李敏", 0, WIN, hero_damage=85_000),
                _player(1, "老狼", "玛法里奥", 0, WIN, healing=47_000),
                _player(2, "队友A", "缝合怪", 0, WIN),
                _player(3, "队友B", "瓦里安", 0, WIN),
                _player(4, "队友C", "源氏", 0, WIN),
                _player(5, "路人甲", "古尔丹", 1, LOSS),
                _player(6, "路人乙", "乌瑟尔", 1, LOSS),
                _player(7, "路人丙", "迪亚波罗", 1, LOSS),
                _player(8, "路人丁", "凯尔萨斯", 1, LOSS),
                _player(9, "路人戊", "缝合怪", 1, LOSS),
            ],
        )
    )

    # Match 3 — ARAM (should be excluded from default-mode queries).
    replays.append(
        _replay(
            3,
            map_name="布莱克西斯禁区",
            mode="ARAM",
            played_at=_BASE_TIME + timedelta(days=2),
            winner_team=0,
            players=[
                _player(0, "阿离", "缝合怪", 0, WIN),
                _player(1, "老狼", "古尔丹", 0, WIN),
                _player(2, "队友A", "李敏", 0, WIN),
                _player(3, "队友B", "瓦里安", 0, WIN),
                _player(4, "队友C", "源氏", 0, WIN),
                _player(5, "敌人A", "乌瑟尔", 1, LOSS),
                _player(6, "敌人B", "迪亚波罗", 1, LOSS),
                _player(7, "敌人C", "凯尔萨斯", 1, LOSS),
                _player(8, "敌人D", "玛法里奥", 1, LOSS),
                _player(9, "敌人E", "源氏", 1, LOSS),
            ],
        )
    )

    return replays


@pytest.fixture
def seeded_store(tmp_path) -> Store:
    """A Store at a temp path seeded with four synthetic matches."""
    store = Store(tmp_path / "test.db")
    for replay in _build_replays():
        store.upsert_replay(replay)
    yield store
    store.close()


@pytest.fixture
def empty_store(tmp_path) -> Store:
    store = Store(tmp_path / "empty.db")
    yield store
    store.close()
