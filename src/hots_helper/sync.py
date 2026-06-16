"""Optional cloud-sync layer for the local replay DB.

The squad shares one Supabase project. Every member's app pushes new
matches up after each ingest and pulls everyone else's down on launch.
Conflicts are deduplicated by ``match_key`` server-side, so it doesn't
matter who scans a given replay first.

Design notes
------------
* The sync code is **completely optional**. If ``supabase_url`` /
  ``supabase_anon_key`` are blank in the config, nothing here runs.
* HTTP-only — no ``supabase-py`` dependency. PostgREST is a stable
  REST API and we only need /rest/v1 + Prefer headers.
* Push uses ``upsert`` (Prefer: resolution=merge-duplicates) keyed on
  the table's primary key, so re-runs are idempotent.
* Pull uses ``inserted_at > <last>`` so we only fetch new rows. The
  ``last_pulled_at`` watermark is stored in a tiny JSON file in the
  user data dir. All three tables carry ``inserted_at``; player_match
  gained it via packaging/migrations/0001 — pre-migration projects fall
  back to a full pull.
* Pulls are paged in small chunks with per-page retry so a single
  dropped TLS stream doesn't waste the whole pull.
* All network IO runs from a worker thread; failures are logged but
  never raised to the UI.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import data_dir
from .db import Store
from .parser.replay import PlayerMatch, Replay

logger = logging.getLogger(__name__)


# Network resilience for pulls. PostgREST happily streams thousands of
# rows in one response, but a single large response (the player_match
# table is ~35 MB) is exactly what a flaky connection drops mid-stream
# with ``SSL UNEXPECTED_EOF_WHILE_READING``. We page in smaller chunks
# and retry each page a few times so one dropped TCP stream no longer
# wastes the whole pull.
_PULL_PAGE_SIZE = 500
_PULL_MAX_RETRIES = 4
_PULL_RETRY_BACKOFF_S = 1.5

# Watermark schema version. Bump this whenever a shipped bug left the
# pull watermarks in a state that needs a one-time reset. On startup, a
# client whose stored ``_v`` is lower clears all ``pull_*`` keys exactly
# once and re-pulls from scratch (inserts are idempotent, so this only
# *adds* the rows an older buggy build failed to land — it never
# duplicates or corrupts existing data). ``push_*`` keys are left alone
# so nobody re-uploads their whole history.
#
# v1: player_match watermark was never persisted (the cloud table had no
#     ``inserted_at``), so every sync re-downloaded the full table and a
#     dropped TLS stream meant new player rows silently never landed.
#     Resetting pull watermarks lets the fixed client backfill them.
_WATERMARK_VERSION = 1


# Tables we sync, in dependency order (replays first because player_match
# references it via ``match_key``).
_REPLAY_COLUMNS = (
    "match_key", "random_seed", "map_name", "mode", "build", "protocol_build",
    "played_at", "duration_s", "winner_team", "bans_team0", "bans_team1",
)

_PLAYER_COLUMNS = ("toon_handle", "display_name", "last_seen_at")

_PLAYER_MATCH_COLUMNS = (
    "match_key", "slot", "toon_handle", "display_name", "hero", "hero_id",
    "skin", "banner", "team", "result",
    "kills", "deaths", "assists", "takedowns", "solo_kills", "level",
    "hero_damage", "siege_damage", "structure_damage",
    "creep_damage", "minion_damage", "minion_kills", "summon_damage",
    "physical_damage", "spell_damage",
    "healing", "self_healing", "damage_taken", "damage_soaked",
    "teamfight_hero_damage", "teamfight_healing", "teamfight_damage_taken",
    "teamfight_escapes",
    "experience_contribution", "time_spent_dead", "time_on_point",
    "merc_camp_captures", "watch_tower_captures", "regen_globes",
    "town_kills", "meta_experience",
    "time_cc_enemy_heroes", "time_stunning_enemy_heroes",
    "time_rooting_enemy_heroes", "time_silencing_enemy_heroes",
    "highest_kill_streak", "multikill", "escapes_performed",
    "vengeances_performed", "outnumbered_deaths", "clutch_heals",
    "protection_given_to_allies", "on_fire_time",
    "talents", "awards", "hero_mastery_tiers",
)


@dataclass
class SyncResult:
    pushed_replays: int
    pushed_players: int
    pushed_player_matches: int
    pulled_replays: int
    pulled_players: int
    pulled_player_matches: int
    errors: list[str]

    @property
    def total_pushed(self) -> int:
        return self.pushed_replays + self.pushed_players + self.pushed_player_matches

    @property
    def total_pulled(self) -> int:
        return self.pulled_replays + self.pulled_players + self.pulled_player_matches


def _strip_nulls(value):
    """Postgres text columns reject U+0000 (NUL bytes). HotS player names
    occasionally include them as control characters from the in-game
    rich-text formatter. Strip them defensively."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


def _watermark_path() -> Path:
    return data_dir() / "sync_watermark.json"


def _read_watermark() -> dict[str, str]:
    p = _watermark_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def _write_watermark(values: dict[str, str]) -> None:
    _watermark_path().write_text(json.dumps(values, indent=2), "utf-8")


def _heal_watermark() -> None:
    """One-time, idempotent reset of pull watermarks after a sync-bug fix.

    Runs once per client whenever the stored ``_v`` is below
    ``_WATERMARK_VERSION``. It drops every ``pull_*`` key (forcing a
    full, from-scratch re-pull that backfills rows an older buggy build
    failed to land) and stamps the new version so it never repeats.
    ``push_*`` keys are preserved so the client doesn't re-upload its
    whole local history. A fresh install (no file yet) is just stamped
    with the current version — there's nothing to heal.
    """
    wm = _read_watermark()
    current = wm.get("_v", 0)
    try:
        current = int(current)
    except (TypeError, ValueError):
        current = 0
    if current >= _WATERMARK_VERSION:
        return
    pull_keys = [k for k in wm if k.startswith("pull_")]
    if pull_keys:
        logger.info(
            "Healing sync watermark v%s→v%s: clearing %d pull key(s) to "
            "backfill rows missed by an older build.",
            current, _WATERMARK_VERSION, len(pull_keys),
        )
    healed = {k: v for k, v in wm.items() if not k.startswith("pull_")}
    healed["_v"] = _WATERMARK_VERSION
    _write_watermark(healed)


# --- Supabase REST helpers -------------------------------------------------


class _RestClient:
    """Minimal Supabase PostgREST client over urllib."""

    def __init__(self, url: str, anon_key: str) -> None:
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers_get = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Accept": "application/json",
        }
        self.headers_write = {
            **self.headers_get,
            "Content-Type": "application/json",
            # Upsert by primary key, return nothing in body for speed.
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }

    def upsert(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        # PostgREST batches up to ~1k rows fine. Chunk to keep URLs and
        # request bodies reasonable.
        chunk_size = 500
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            req = urllib.request.Request(
                f"{self.base}/{table}",
                data=json.dumps(chunk, ensure_ascii=False).encode("utf-8"),
                headers=self.headers_write,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read()

    def _get_page(self, table: str, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Fetch one page, retrying on transient network failures.

        A dropped TCP/TLS stream (``SSL UNEXPECTED_EOF_WHILE_READING``,
        connection reset, timeout) raises ``URLError``; we retry that
        page a few times with linear backoff before giving up. HTTP 4xx
        responses are *not* retried — they won't fix themselves.
        """
        qs = urllib.parse.urlencode(params, safe=":,.~")
        url = f"{self.base}/{table}?{qs}"
        last_err: Exception | None = None
        for attempt in range(1, _PULL_MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(url, headers=self.headers_get,
                                             method="GET")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8") or "[]")
            except urllib.error.HTTPError:
                # Server said no (4xx/5xx with a body) — surface it, don't retry.
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                if attempt < _PULL_MAX_RETRIES:
                    logger.warning(
                        "pull page failed (%s), retry %d/%d: %s",
                        table, attempt, _PULL_MAX_RETRIES - 1, e,
                    )
                    time.sleep(_PULL_RETRY_BACKOFF_S * attempt)
        assert last_err is not None
        raise last_err

    def select_since(self, table: str, since_iso: str | None,
                     order_col: str = "inserted_at") -> list[dict[str, Any]]:
        """Fetch all rows where ``order_col`` > ``since_iso`` (or all rows).

        Paged in small chunks with per-page retry so one dropped stream
        doesn't waste the whole pull. Ordering by ``order_col`` keeps
        offset paging stable as long as the column is unique enough; for
        non-unique columns (e.g. a batch sharing one ``inserted_at``) we
        still converge because every chunk advances the offset.
        """
        params: list[tuple[str, str]] = [("select", "*"), ("order", order_col)]
        if since_iso:
            # PostgREST filter syntax: column=op.value — gt = greater than.
            params.append((order_col, f"gt.{since_iso}"))
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            paged = params + [
                ("limit", str(_PULL_PAGE_SIZE)),
                ("offset", str(offset)),
            ]
            batch = self._get_page(table, paged)
            rows.extend(batch)
            if len(batch) < _PULL_PAGE_SIZE:
                break
            offset += _PULL_PAGE_SIZE
        return rows


# --- Engine ----------------------------------------------------------------


ProgressCallback = Callable[[str], None] | None


class CloudSync:
    """High-level sync operations against Supabase."""

    def __init__(self, store: Store, url: str, anon_key: str) -> None:
        self.store = store
        self._client = _RestClient(url, anon_key)
        self._lock = threading.Lock()

    # --- public API ---------------------------------------------------------

    def is_enabled(self) -> bool:
        return bool(self._client.base) and bool(self._client.headers_get.get("apikey"))

    def sync_now(self, progress: ProgressCallback = None) -> SyncResult:
        """One full round: push local-only rows, then pull cloud-newer rows."""
        with self._lock:
            # Self-heal stale pull watermarks left by older buggy builds
            # (runs at most once per client; see _heal_watermark).
            _heal_watermark()
            errors: list[str] = []
            pushed = self._push_all(progress, errors)
            pulled = self._pull_all(progress, errors)
            return SyncResult(
                pushed_replays=pushed[0], pushed_players=pushed[1],
                pushed_player_matches=pushed[2],
                pulled_replays=pulled[0], pulled_players=pulled[1],
                pulled_player_matches=pulled[2],
                errors=errors,
            )

    # --- push --------------------------------------------------------------

    def _push_all(self, progress: ProgressCallback,
                  errors: list[str]) -> tuple[int, int, int]:
        # Strategy: push everything that hasn't been pushed yet. We track
        # the last-pushed ``inserted_at`` per table in the watermark file
        # so the next call only sends new rows.
        watermark = _read_watermark()

        # All push queries filter ``file_hash NOT LIKE 'cloud:%'`` — cloud-
        # pulled replays carry a synthetic file_hash of ``cloud:<match_key>``
        # while locally-ingested ones carry a real SHA-256. Without this
        # filter, every launch after a fresh pull would re-upload the
        # ~7000 rows we just downloaded (the server upserts so data
        # doesn't change, but it's a pointless ~10MB round-trip).

        pushed_r = self._push_table(
            table="replays",
            columns=_REPLAY_COLUMNS,
            local_query="""
                SELECT match_key, random_seed, map_name, mode, build,
                       protocol_build, played_at, duration_s, winner_team,
                       bans_team0, bans_team1
                FROM replays
                WHERE played_at > ?
                  AND file_hash NOT LIKE 'cloud:%'
                ORDER BY played_at
            """,
            since=watermark.get("push_replays", "1970-01-01T00:00:00+00:00"),
            since_field="played_at",
            label="replays",
            progress=progress, errors=errors,
            watermark=watermark, watermark_key="push_replays",
        )

        # ``players`` has no direct origin column. Push only those who
        # have at least one *local-origin* match — i.e. someone we've
        # actually seen via a replay we ingested. Players that came in
        # purely from a cloud pull get skipped (they're already on the
        # server by definition).
        pushed_p = self._push_table(
            table="players",
            columns=_PLAYER_COLUMNS,
            local_query="""
                SELECT p.toon_handle, p.display_name, p.last_seen_at
                FROM players p
                WHERE p.last_seen_at > ?
                  AND EXISTS (
                    SELECT 1 FROM player_match pm
                    JOIN replays r ON r.id = pm.replay_id
                    WHERE pm.toon_handle = p.toon_handle
                      AND r.file_hash NOT LIKE 'cloud:%'
                  )
                ORDER BY p.last_seen_at
            """,
            since=watermark.get("push_players", "1970-01-01T00:00:00+00:00"),
            since_field="last_seen_at",
            label="players",
            progress=progress, errors=errors,
            watermark=watermark, watermark_key="push_players",
        )

        pushed_pm = self._push_player_matches(
            since=watermark.get("push_player_match", "1970-01-01T00:00:00+00:00"),
            progress=progress, errors=errors,
            watermark=watermark,
        )

        _write_watermark(watermark)
        return pushed_r, pushed_p, pushed_pm

    def _push_table(self, *, table: str, columns: tuple[str, ...],
                    local_query: str, since: str, since_field: str,
                    label: str, progress: ProgressCallback,
                    errors: list[str], watermark: dict[str, str],
                    watermark_key: str) -> int:
        try:
            rows = self.store.conn.execute(local_query, (since,)).fetchall()
        except Exception as e:
            errors.append(f"{label} read failed: {e}")
            return 0
        if not rows:
            return 0
        if progress:
            progress(f"pushing {len(rows)} {label}…")
        records = [
            {col: _strip_nulls(row[col]) for col in columns}
            for row in rows
        ]
        try:
            self._client.upsert(table, records)
        except urllib.error.HTTPError as e:
            errors.append(f"{label} push HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
            return 0
        except Exception as e:
            errors.append(f"{label} push failed: {type(e).__name__}: {e}")
            return 0
        # Advance watermark to the newest row we just pushed.
        latest = max(row[since_field] for row in records)
        watermark[watermark_key] = latest
        return len(records)

    def _push_player_matches(self, *, since: str, progress: ProgressCallback,
                             errors: list[str], watermark: dict[str, str]) -> int:
        # player_match rows don't carry a timestamp themselves; we use the
        # parent replay's played_at via JOIN. Send all rows whose replay
        # is newer than the last-pushed timestamp.
        # Local schema joins via ``replay_id`` (the auto-increment column on
        # replays), not ``match_key``. Cloud schema is keyed on match_key
        # because there's no global replay_id across squad members. Bridge
        # them by joining on replays.id and substituting r.match_key in
        # output.
        try:
            rows = self.store.conn.execute("""
                SELECT r.match_key AS match_key, pm.slot, pm.toon_handle,
                       pm.display_name,
                       pm.hero, pm.hero_id, pm.skin, pm.banner, pm.team, pm.result,
                       pm.kills, pm.deaths, pm.assists, pm.takedowns, pm.solo_kills,
                       pm.level,
                       pm.hero_damage, pm.siege_damage, pm.structure_damage,
                       pm.creep_damage, pm.minion_damage, pm.minion_kills,
                       pm.summon_damage, pm.physical_damage, pm.spell_damage,
                       pm.healing, pm.self_healing, pm.damage_taken,
                       pm.damage_soaked,
                       pm.teamfight_hero_damage, pm.teamfight_healing,
                       pm.teamfight_damage_taken, pm.teamfight_escapes,
                       pm.experience_contribution, pm.time_spent_dead,
                       pm.time_on_point, pm.merc_camp_captures,
                       pm.watch_tower_captures, pm.regen_globes,
                       pm.town_kills, pm.meta_experience,
                       pm.time_cc_enemy_heroes, pm.time_stunning_enemy_heroes,
                       pm.time_rooting_enemy_heroes, pm.time_silencing_enemy_heroes,
                       pm.highest_kill_streak, pm.multikill, pm.escapes_performed,
                       pm.vengeances_performed, pm.outnumbered_deaths,
                       pm.clutch_heals, pm.protection_given_to_allies,
                       pm.on_fire_time, pm.talents, pm.awards, pm.hero_mastery_tiers,
                       r.played_at AS replay_played_at
                FROM player_match pm
                JOIN replays r ON r.id = pm.replay_id
                WHERE r.played_at > ?
                  AND r.match_key != ''
                  AND r.file_hash NOT LIKE 'cloud:%'
                ORDER BY r.played_at
            """, (since,)).fetchall()
        except Exception as e:
            errors.append(f"player_match read failed: {e}")
            return 0
        if not rows:
            return 0
        if progress:
            progress(f"pushing {len(rows)} player_match rows…")
        records: list[dict[str, Any]] = []
        latest_replay_at = since
        for row in rows:
            d = {col: _strip_nulls(row[col]) for col in _PLAYER_MATCH_COLUMNS}
            records.append(d)
            if row["replay_played_at"] > latest_replay_at:
                latest_replay_at = row["replay_played_at"]
        try:
            self._client.upsert("player_match", records)
        except urllib.error.HTTPError as e:
            errors.append(f"player_match push HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
            return 0
        except Exception as e:
            errors.append(f"player_match push failed: {type(e).__name__}: {e}")
            return 0
        watermark["push_player_match"] = latest_replay_at
        return len(records)

    # --- pull --------------------------------------------------------------

    def _pull_all(self, progress: ProgressCallback,
                  errors: list[str]) -> tuple[int, int, int]:
        watermark = _read_watermark()
        replays = self._pull_replays(watermark, progress, errors)
        players = self._pull_players(watermark, progress, errors)
        matches = self._pull_player_matches(watermark, progress, errors)
        _write_watermark(watermark)
        return replays, players, matches

    def _pull_replays(self, watermark: dict[str, str],
                      progress: ProgressCallback, errors: list[str]) -> int:
        since = watermark.get("pull_replays")
        try:
            rows = self._client.select_since("replays", since)
        except Exception as e:
            errors.append(f"pull replays failed: {type(e).__name__}: {e}")
            return 0
        if not rows:
            return 0
        if progress:
            progress(f"applying {len(rows)} replays from cloud…")
        applied = 0
        with self.store._lock:
            for r in rows:
                # Skip if we already have this match locally; otherwise
                # insert. Local replays have a file_hash + file_path that
                # cloud rows don't, so we synthesize placeholders.
                existing = self.store.conn.execute(
                    "SELECT 1 FROM replays WHERE match_key = ?",
                    (r["match_key"],),
                ).fetchone()
                if existing is not None:
                    continue
                self.store.conn.execute(
                    """
                    INSERT INTO replays
                        (file_hash, match_key, random_seed, file_path,
                         map_name, mode, build, protocol_build,
                         played_at, duration_s, winner_team,
                         bans_team0, bans_team1)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"cloud:{r['match_key']}",
                        r["match_key"],
                        int(r.get("random_seed") or 0),
                        "",  # file_path is unknown for cloud rows
                        r["map_name"], r["mode"],
                        int(r["build"]), int(r["protocol_build"]),
                        r["played_at"],
                        int(r["duration_s"]), int(r["winner_team"]),
                        r.get("bans_team0") or "",
                        r.get("bans_team1") or "",
                    ),
                )
                applied += 1
            self.store.conn.commit()
        # Watermark forward.
        latest = max(r.get("inserted_at") or r.get("played_at") for r in rows)
        watermark["pull_replays"] = latest
        return applied

    def _pull_players(self, watermark: dict[str, str],
                      progress: ProgressCallback, errors: list[str]) -> int:
        since = watermark.get("pull_players")
        try:
            rows = self._client.select_since("players", since)
        except Exception as e:
            errors.append(f"pull players failed: {type(e).__name__}: {e}")
            return 0
        if not rows:
            return 0
        if progress:
            progress(f"applying {len(rows)} players from cloud…")
        with self.store._lock:
            for r in rows:
                self.store.conn.execute(
                    """
                    INSERT INTO players (toon_handle, display_name, last_seen_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(toon_handle) DO UPDATE SET
                        display_name = CASE
                            WHEN excluded.last_seen_at >= players.last_seen_at
                                THEN excluded.display_name
                            ELSE players.display_name
                        END,
                        last_seen_at = CASE
                            WHEN excluded.last_seen_at >= players.last_seen_at
                                THEN excluded.last_seen_at
                            ELSE players.last_seen_at
                        END
                    """,
                    (r["toon_handle"], r["display_name"], r["last_seen_at"]),
                )
            self.store.conn.commit()
        latest = max(r.get("inserted_at") or r.get("last_seen_at") for r in rows)
        watermark["pull_players"] = latest
        return len(rows)

    def _pull_player_matches(self, watermark: dict[str, str],
                             progress: ProgressCallback,
                             errors: list[str]) -> int:
        # The cloud ``player_match`` table carries an ``inserted_at``
        # timestamp (added by the 0001 migration); we anchor incremental
        # pulls on it. Ordering by ``inserted_at`` makes offset paging
        # stable and lets us advance the watermark to the newest row we
        # actually pulled, so subsequent syncs only fetch new rows
        # instead of re-downloading the whole ~35 MB table every launch.
        since = watermark.get("pull_player_match")
        # Whether the cloud table exposes ``inserted_at`` (post-migration).
        # When it doesn't yet, fall back to a full, watermark-less pull
        # ordered by the primary key so a not-yet-migrated project keeps
        # working — just without the incremental optimisation.
        has_inserted_at = True
        try:
            rows = self._client.select_since("player_match", since,
                                             order_col="inserted_at")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 400 and "inserted_at" in body:
                # Column missing — pre-migration project. Full pull.
                logger.warning(
                    "player_match has no inserted_at column yet; falling "
                    "back to a full pull (run the 0001 migration to enable "
                    "incremental sync)."
                )
                has_inserted_at = False
                try:
                    rows = self._client.select_since(
                        "player_match", None, order_col="match_key")
                except Exception as e2:
                    errors.append(
                        f"pull player_match failed: {type(e2).__name__}: {e2}")
                    return 0
            else:
                errors.append(f"pull player_match failed: HTTP {e.code}: {body[:200]}")
                return 0
        except Exception as e:
            errors.append(f"pull player_match failed: {type(e).__name__}: {e}")
            return 0
        if not rows:
            return 0
        if progress:
            progress(f"applying {len(rows)} player_match rows from cloud…")
        # Map each cloud row's match_key to the local replay_id; rows whose
        # replay we never pulled are dropped (will retry next sync). The
        # ``toon_handle`` FK to ``players`` is enforced too, but we don't
        # rely on the prior _pull_players step having seen that handle —
        # an old player can show up in a fresh match-from-someone-else's-
        # PC without their players-row exceeding the watermark, which
        # would crash with FOREIGN KEY constraint failed. Defensively
        # upsert a stub player from each player_match row before
        # inserting the match itself.
        applied = 0
        skipped_no_replay = 0
        fk_errors = 0
        # Track the earliest ``inserted_at`` we had to skip (its parent
        # replay wasn't replicated yet, or an FK check failed). We must
        # NOT advance the watermark past it, or the next ``gt.<wm>`` pull
        # would never re-fetch it and the row would be lost forever.
        earliest_skipped: str | None = None

        def _mark_skipped(row: dict[str, Any]) -> None:
            nonlocal earliest_skipped
            ts = row.get("inserted_at") or ""
            if ts and (earliest_skipped is None or ts < earliest_skipped):
                earliest_skipped = ts

        with self.store._lock:
            for r in rows:
                rid = self.store.conn.execute(
                    "SELECT id FROM replays WHERE match_key = ?",
                    (r["match_key"],),
                ).fetchone()
                if rid is None:
                    skipped_no_replay += 1
                    _mark_skipped(r)
                    continue
                replay_id = int(rid["id"])
                # Skip if we already have this slot.
                exists = self.store.conn.execute(
                    "SELECT 1 FROM player_match WHERE replay_id=? AND slot=?",
                    (replay_id, int(r["slot"])),
                ).fetchone()
                if exists is not None:
                    continue
                # Ensure the FK target exists. INSERT OR IGNORE so we
                # don't clobber a more-recent display_name we already
                # have locally; the proper update flow lives in
                # _pull_players above, this is just a safety net.
                toon = _strip_nulls(r.get("toon_handle") or "")
                if toon:
                    self.store.conn.execute(
                        """INSERT OR IGNORE INTO players
                               (toon_handle, display_name, last_seen_at)
                           VALUES (?, ?, ?)""",
                        (
                            toon,
                            _strip_nulls(r.get("display_name") or toon),
                            r.get("inserted_at") or "1970-01-01T00:00:00+00:00",
                        ),
                    )
                # Build the row for the *local* schema: drop match_key
                # (the local table has no such column — the cloud uses it
                # as primary key, but locally we relate via replay_id).
                local_cols = [c for c in _PLAYER_MATCH_COLUMNS if c != "match_key"]
                cols = ["replay_id"] + local_cols
                values = [replay_id] + [
                    _strip_nulls(
                        r.get(c, 0 if not isinstance(r.get(c), str) else "")
                    )
                    for c in local_cols
                ]
                placeholders = ",".join("?" for _ in cols)
                try:
                    self.store.conn.execute(
                        f"INSERT OR IGNORE INTO player_match ({','.join(cols)}) "
                        f"VALUES ({placeholders})",
                        tuple(values),
                    )
                except sqlite3.IntegrityError as e:
                    # Don't let a single bad row abort the entire pull.
                    # Most likely cause is a stale watermark pointing
                    # past a row whose FK targets we haven't replicated
                    # yet; the next sync will pick it up once the
                    # missing parents arrive.
                    fk_errors += 1
                    _mark_skipped(r)
                    if fk_errors <= 5:
                        errors.append(
                            f"player_match FK skip "
                            f"(match_key={r.get('match_key')!r}, "
                            f"toon={toon!r}): {e}"
                        )
                    continue
                applied += 1
            self.store.conn.commit()
        if skipped_no_replay or fk_errors:
            logger.info(
                "player_match pull: applied=%d skipped_no_replay=%d fk_errors=%d",
                applied, skipped_no_replay, fk_errors,
            )
        # Advance the watermark to the newest row pulled — but never to or
        # past the earliest row we had to skip, since the next pull uses a
        # strict ``gt.<watermark>`` filter and would otherwise never
        # re-fetch the skipped row once its parent replay arrives.
        #
        # Only meaningful when the server has ``inserted_at``. On the
        # pre-migration fallback path the rows carry no timestamp, so we
        # leave the watermark unset and keep doing full pulls until the
        # migration lands.
        if has_inserted_at:
            if earliest_skipped is None:
                latest = max((r.get("inserted_at") or "" for r in rows), default="")
            else:
                # Largest inserted_at strictly older than the earliest skip.
                safe = [
                    ts for r in rows
                    if (ts := (r.get("inserted_at") or "")) and ts < earliest_skipped
                ]
                # If nothing predates the skip, leave the watermark
                # untouched rather than regressing it.
                latest = max(safe) if safe else (since or "")
            if latest:
                watermark["pull_player_match"] = latest
        return applied


# --- Convenience -----------------------------------------------------------


def make_sync(store: Store, url: str, anon_key: str) -> CloudSync | None:
    if not url or not anon_key:
        return None
    return CloudSync(store, url, anon_key)
