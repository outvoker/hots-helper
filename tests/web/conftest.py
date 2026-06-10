"""Fixtures for the web-layer tests.

A :class:`TestClient` wired to the real FastAPI app, but with a
:class:`StoreProvider` pointed at a seeded temp Store instead of
Supabase — so the tests never touch the network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hots_helper.web.app import create_app
from hots_helper.web.data import StoreProvider


class _SeededProvider(StoreProvider):
    """Provider that wraps an already-built Store (from the seeded_store
    fixture) instead of syncing from the cloud."""

    def __init__(self, store):
        super().__init__()
        self._preset = store

    def startup(self) -> None:
        self._store = self._preset
        self._rebuild_baseline()

    def shutdown(self) -> None:
        # The seeded_store fixture owns the connection's lifecycle.
        self._baseline = None


@pytest.fixture
def client(seeded_store):
    provider = _SeededProvider(seeded_store)
    app = create_app(provider=provider)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def empty_client(empty_store):
    provider = _SeededProvider(empty_store)
    app = create_app(provider=provider)
    with TestClient(app) as c:
        yield c
