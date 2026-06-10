"""Guard that serializers include the @property fields asdict() would drop."""

from __future__ import annotations

import pytest

from hots_helper.lookup import lookup_players
from hots_helper.player_rank import compute_player_rankings
from hots_helper.weekly_report import build_weekly_report
from hots_helper.web import serialize

pytestmark = pytest.mark.unit


def test_player_summary_has_derived_winrates(seeded_store):
    summaries = lookup_players(seeded_store, ["阿离"])["阿离"]
    d = serialize.player_summary(summaries[0])
    assert "winrate" in d
    assert "recent_winrate" in d
    assert "map_winrate" in d
    assert set(d["overall_kda"]) == {"k", "d", "a"}


def test_player_rank_row_has_kda(seeded_store):
    rows = compute_player_rankings(seeded_store, min_games=1)
    if rows:
        d = serialize.player_rank_row(rows[0])
        assert "kda" in d
        assert "power" in d


def test_weekly_report_has_derived_fields(seeded_store):
    report = build_weekly_report(seeded_store, days=3650)
    d = serialize.weekly_report(report, brief="x")
    assert "games_delta" in d["overview"]
    assert "winrate_delta_pp" in d["overview"]
    assert "winrate" in d["overview"]["current"]
    assert "is_empty" in d["longest_win_streak"]
