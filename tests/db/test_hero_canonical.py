"""Hero-name simplified/traditional folding in aggregate queries.

A TW/KR-localised replay records hero names in 繁體 (e.g. "維拉"), which
otherwise split into a separate leaderboard row from the zh-CN "维拉"
and dilute the hero's stats. The store registers a ``canon_hero`` SQL
function and groups on it, so both spellings merge — at query time,
without rewriting the stored (possibly dirty) rows.
"""

from __future__ import annotations

from datetime import datetime, timezone

from hots_helper.db import Store
from hots_helper.parser.replay import PlayerMatch, Replay

WIN, LOSS = 1, 2
_T = datetime(2026, 5, 1, 20, 0, 0, tzinfo=timezone.utc)


def _pm(slot, hero, team, result):
    return PlayerMatch(
        slot=slot, name=f"p{slot}", toon_handle=f"1-Hero-1-p{slot}",
        hero=hero, hero_id="Vala", skin="", banner="", team=team,
        result=result, kills=5, deaths=3, assists=8, takedowns=13,
        solo_kills=1, level=20, hero_damage=60_000, siege_damage=20_000,
        structure_damage=2_000, healing=0, self_healing=0,
        damage_taken=15_000, damage_soaked=0, experience_contribution=30_000,
        time_cc_enemy_heroes=2, talents=["T1"], awards=[],
    )


def _replay(idx, players, winner=0):
    return Replay(
        file_path=f"/tmp/r{idx}.StormReplay", file_hash=f"h{idx}",
        match_key=f"m{idx}", random_seed=idx, map_name="白银城",
        mode="Storm League", build=90000, protocol_build=90000,
        played_at=_T, duration_seconds=1200, winner_team=winner,
        bans=[], bans_team0=[], bans_team1=[], players=players,
    )


def _store(tmp_path):
    store = Store(tmp_path / "canon.db")
    # One replay where slot 0 is the 繁體 "維拉", another where it's the
    # zh-CN "维拉". Everyone else is filler so the replay is valid.
    fillers = [_pm(s, "李敏", 0 if s < 5 else 1, WIN if s < 5 else LOSS)
               for s in range(1, 10)]
    store.upsert_replay(_replay(1, [_pm(0, "維拉", 0, WIN)] + fillers))
    store.upsert_replay(_replay(2, [_pm(0, "维拉", 0, WIN)] + fillers))
    return store


def test_aggregate_merges_traditional_and_simplified(tmp_path):
    store = _store(tmp_path)
    rows = store.hero_aggregate_stats()
    vala = [r for r in rows if r["hero"] in ("维拉", "維拉")]
    # Exactly one merged row, displayed in zh-CN, counting both games.
    assert len(vala) == 1
    assert vala[0]["hero"] == "维拉"
    assert vala[0]["games"] == 2
    store.close()


def test_hero_detail_query_matches_both_spellings(tmp_path):
    store = _store(tmp_path)
    # Querying by the canonical name finds the 繁體-stored game too.
    games, wins = store.global_hero_winrate("维拉")
    assert games == 2 and wins == 2
    store.close()


def test_all_heroes_has_no_traditional_duplicate(tmp_path):
    store = _store(tmp_path)
    names = [r["hero"] for r in store.all_heroes()]
    assert "維拉" not in names
    assert names.count("维拉") == 1
    store.close()
