"""Tests for Store.list_matches + match_roster_brief."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_list_matches_default_excludes_aram_sorted_desc(seeded_store):
    rows, total = seeded_store.list_matches()
    # Default mode filter = Storm League → 3 of the 4 seeded matches.
    assert total == 3
    assert len(rows) == 3
    played = [r["played_at"] for r in rows]
    assert played == sorted(played, reverse=True)


def test_list_matches_mode_none_includes_aram(seeded_store):
    rows, total = seeded_store.list_matches(mode=None)
    assert total == 4


def test_list_matches_filter_by_map(seeded_store):
    rows, total = seeded_store.list_matches(map_name="白银城")
    assert total == 2
    assert all(r["map_name"] == "白银城" for r in rows)


def test_list_matches_filter_by_explicit_mode(seeded_store):
    rows, total = seeded_store.list_matches(mode="ARAM")
    assert total == 1
    assert rows[0]["map_name"] == "布莱克西斯禁区"


def test_list_matches_pagination(seeded_store):
    page1, total = seeded_store.list_matches(limit=2, offset=0)
    page2, _ = seeded_store.list_matches(limit=2, offset=2)
    assert total == 3
    assert len(page1) == 2
    assert len(page2) == 1
    ids = {r["id"] for r in page1} | {r["id"] for r in page2}
    assert len(ids) == 3  # no overlap


def test_list_matches_filter_by_player_handle(seeded_store):
    rows, total = seeded_store.list_matches(player="1-Hero-1-阿离")
    assert total == 3  # 阿离 played all 3 SL games


def test_list_matches_filter_by_player_display_name(seeded_store):
    rows, total = seeded_store.list_matches(player="老狼")
    assert total == 3


def test_list_matches_result_requires_player_perspective(seeded_store):
    # 阿离 won match 0 and match 2, lost match 1.
    wins, total_w = seeded_store.list_matches(player="阿离", result=1)
    losses, total_l = seeded_store.list_matches(player="阿离", result=0)
    assert total_w == 2
    assert total_l == 1


def test_list_matches_date_range(seeded_store):
    # Only the first match is before 2026-05-01T00:30Z + ... use a cutoff.
    rows, total = seeded_store.list_matches(since_iso="2026-05-02T00:00:00+00:00")
    # Match 2 is on 2026-05-02; matches 0 and 1 are on 2026-05-01.
    assert total == 1


def test_match_roster_brief_batches(seeded_store):
    ids = [r["id"] for r in seeded_store.list_matches()[0]]
    brief = seeded_store.match_roster_brief(ids)
    # One entry per (replay, player) — 10 per match.
    assert len(brief) == len(ids) * 10
    sample = brief[0]
    assert {"replay_id", "team", "hero", "display_name"} <= set(sample.keys())


def test_match_roster_brief_empty_input(seeded_store):
    assert seeded_store.match_roster_brief([]) == []
