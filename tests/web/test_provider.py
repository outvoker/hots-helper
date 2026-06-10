"""StoreProvider behaviour, especially the no-Supabase fallback."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_empty_db_still_serves(empty_client):
    assert empty_client.get("/api/health").json()["replays"] == 0
    assert empty_client.get("/api/heroes").json() == []
    assert empty_client.get("/api/matches").json()["total"] == 0
    assert empty_client.get("/api/rankings/players").json() == []


def test_provider_without_cloud_does_not_sync(tmp_path):
    from hots_helper.web.data import StoreProvider

    provider = StoreProvider(db_path=str(tmp_path / "x.db"))
    assert provider.syncs_from_cloud is False
    provider.startup()
    try:
        assert provider.store().count_replays() == 0
    finally:
        provider.shutdown()
