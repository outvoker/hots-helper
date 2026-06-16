"""Tests for the one-time pull-watermark self-heal.

A shipped bug (v0) never persisted the ``player_match`` pull watermark,
so older builds re-downloaded the whole table and silently dropped rows
when the response was truncated mid-stream. :func:`_heal_watermark`
clears stale ``pull_*`` keys exactly once per client so a fixed build
backfills the missed rows, without disturbing ``push_*`` keys or
repeating on later runs.
"""

from __future__ import annotations

import json

import pytest

import hots_helper.sync as sync


@pytest.fixture
def wm_file(tmp_path, monkeypatch):
    """Point the watermark helpers at a throwaway file."""
    path = tmp_path / "sync_watermark.json"
    monkeypatch.setattr(sync, "_watermark_path", lambda: path)
    return path


def _write(path, data):
    path.write_text(json.dumps(data), "utf-8")


def _read(path):
    return json.loads(path.read_text("utf-8"))


def test_legacy_file_clears_pull_keeps_push(wm_file):
    # Arrange: a pre-version file with both pull and push watermarks.
    _write(wm_file, {
        "push_replays": "2026-05-21T13:37:03+00:00",
        "push_player_match": "2026-05-21T13:37:03+00:00",
        "pull_replays": "2026-06-16T14:22:14+00:00",
        "pull_players": "2026-06-16T14:23:59+00:00",
    })

    # Act
    sync._heal_watermark()

    # Assert: pull keys gone, push keys intact, version stamped.
    result = _read(wm_file)
    assert result["_v"] == sync._WATERMARK_VERSION
    assert "pull_replays" not in result
    assert "pull_players" not in result
    assert result["push_replays"] == "2026-05-21T13:37:03+00:00"
    assert result["push_player_match"] == "2026-05-21T13:37:03+00:00"


def test_heal_is_idempotent(wm_file):
    # Arrange: already healed, then a normal sync re-wrote a pull key.
    _write(wm_file, {
        "_v": sync._WATERMARK_VERSION,
        "push_replays": "2026-05-21T13:37:03+00:00",
        "pull_replays": "2026-06-16T14:22:14+00:00",
    })

    # Act
    sync._heal_watermark()

    # Assert: the freshly-written pull watermark is NOT cleared again.
    result = _read(wm_file)
    assert result["pull_replays"] == "2026-06-16T14:22:14+00:00"


def test_fresh_install_just_stamps_version(wm_file):
    # Arrange: no file at all.
    assert not wm_file.exists()

    # Act
    sync._heal_watermark()

    # Assert: nothing to heal, just the version marker.
    assert _read(wm_file) == {"_v": sync._WATERMARK_VERSION}


def test_corrupt_version_value_treated_as_legacy(wm_file):
    # Arrange: a garbage _v should be treated as "older than current".
    _write(wm_file, {"_v": "oops", "pull_replays": "2026-06-16T14:22:14+00:00"})

    # Act
    sync._heal_watermark()

    # Assert: healed.
    result = _read(wm_file)
    assert result["_v"] == sync._WATERMARK_VERSION
    assert "pull_replays" not in result
