"""Password gate middleware."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from hots_helper.web.app import create_app

from .conftest import _SeededProvider

pytestmark = pytest.mark.integration


def _basic(password: str, user: str = "squad") -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def gated_client(seeded_store, monkeypatch):
    monkeypatch.setenv("HOTS_ACCESS_PASSWORD", "s3cret")
    app = create_app(provider=_SeededProvider(seeded_store))
    with TestClient(app) as c:
        yield c


def test_health_is_exempt(gated_client):
    assert gated_client.get("/api/health").status_code == 200


def test_missing_password_rejected(gated_client):
    r = gated_client.get("/api/stats")
    assert r.status_code == 401
    assert "Basic" in r.headers.get("WWW-Authenticate", "")


def test_wrong_password_rejected(gated_client):
    assert gated_client.get("/api/stats", headers=_basic("nope")).status_code == 401


def test_correct_password_allowed(gated_client):
    assert gated_client.get("/api/stats", headers=_basic("s3cret")).status_code == 200


def test_malformed_header_rejected(gated_client):
    r = gated_client.get("/api/stats", headers={"Authorization": "Bearer x"})
    assert r.status_code == 401
