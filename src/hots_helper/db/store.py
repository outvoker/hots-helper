"""SQLite store for HotS replay ingest + player lookup."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from ..hero_roles import canonical_hero
from ..parser.replay import PlayerMatch, Replay

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _canon_hero_sql(hero: str | None) -> str | None:
    """SQLite-registered wrapper around :func:`canonical_hero`.

    Passes ``NULL`` straight through (SQLite hands us ``None``) so the
    function is safe on any column.
    """
    if hero is None:
        return None
    return canonical_hero(hero)

# Default mode filter applied to every stats query. ARAM games are kept in the
# DB (useful for player-history lookups) but we never use them for BP / winrate
# analysis because hero pool is random and draft mechanics are absent.
DEFAULT_MODE_FILTER: tuple[str, ...] = ("Storm League",)


# Per-metric "contribution thresholds" applied when averaging stats
# that are role-specific. Matches below the threshold count as
# "didn't do this role" and get excluded from the per-player average,
# so a flex queue's healing average reflects their healing games and
# not their assassin games. Tuned by feel against real squad data.
#
# Why each threshold exists:
#   healing       — Mal'Ganis / Sonya leech ~500 self-healing baseline
#                   even on assassin games; > 1000 cuts that noise.
#   damage_soaked — Abathur / Probius passively rack up small soak
#                   while no real soaker is present; > 5000 cuts grazes.
#   structure_dmg — single missed Q on a fort wall = ~500 dmg; > 1000
#                   filters incidental hits.
#   siege_damage  — clearing one minion wave = ~3000 dmg; > 5000 means
#                   "actually pushing".
#   damage_taken  — every hero takes hits, but only tanks take real
#                   pressure: > 30000 marks "frontline that round".
#   cc            — every stun ticks ~0.3-0.5s; > 1.0s = real CC chains
#                   rather than incidental disruptors.
_METRIC_CONTRIB_THRESHOLDS: dict[str, int] = {
    "healing": 1000,
    "damage_soaked": 5000,
    "structure_damage": 1000,
    "siege_damage": 5000,
    "damage_taken": 30000,
    "time_cc_enemy_heroes": 1,
}


# Upper sanity bound for any per-match cumulative metric. Some
# replays (notably old Abathur / Gall games) record a uint32 overflow
# sentinel like 4294967295 in damage_soaked or healing; those values
# poison every average if not filtered. A real game's per-player soak
# tops out around 250k, so 10 million is a safe cliff that throws
# away the sentinels without clipping any real performances.
_METRIC_SANITY_MAX = 10_000_000


def _avg_when(metric: str, *, alias: str) -> str:
    """Build a SQL fragment that averages ``pm.<metric>`` only over
    matches where the contribution clears the role threshold and
    falls below :data:`_METRIC_SANITY_MAX`.

    Returns ``"… AS <alias>"`` so callers drop it straight into a
    SELECT clause. Metrics without a configured threshold get a
    plain ``AVG`` — most stats (kills, hero_damage, …) every hero
    scores on, so threshold gating would just throw away signal.
    """
    threshold = _METRIC_CONTRIB_THRESHOLDS.get(metric)
    if threshold is None:
        # Still apply the sanity bound — uint32 sentinels can show up
        # in any cumulative-damage field, not just the threshold-gated
        # ones.
        return (
            f"AVG(CASE WHEN pm.{metric} < {_METRIC_SANITY_MAX} "
            f"         THEN pm.{metric} END) AS {alias}"
        )
    # NULLIF(..., 0) returns NULL for the no-contribution case; SQLite
    # then propagates NULL through divide which we coalesce to 0 in
    # the result-row fetcher.
    return (
        f"SUM(CASE WHEN pm.{metric} > {threshold} "
        f"          AND  pm.{metric} < {_METRIC_SANITY_MAX} "
        f"          THEN pm.{metric} ELSE 0 END) * 1.0 / "
        f"NULLIF(SUM(CASE WHEN pm.{metric} > {threshold} "
        f"                AND  pm.{metric} < {_METRIC_SANITY_MAX} "
        f"               THEN 1 ELSE 0 END), 0) AS {alias}"
    )


def _mode_clause(
    mode_filter: tuple[str, ...] | None, alias: str = "r"
) -> tuple[str, list[Any]]:
    """Build a ``WHERE``-ready clause + params for filtering by mode.

    Callers pass ``None`` to disable filtering entirely (for all-history
    lookups); anything else filters to those modes.
    """
    if mode_filter is None:
        return "", []
    if not mode_filter:
        return "", []
    placeholders = ",".join("?" for _ in mode_filter)
    return f" AND {alias}.mode IN ({placeholders})", list(mode_filter)


def _levenshtein(a: str, b: str) -> int:
    """Classic dynamic-programming edit distance. Cheap enough for short names."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (ca != cb),
            )
        prev = curr
    return prev[-1]


def default_db_path() -> Path:
    """User-data DB location, shared between CLI and UI.

    Lives in the platform-standard user-data dir
    (``%APPDATA%\\hots-helper\\hots.db`` on Windows,
    ``~/Library/Application Support/hots-helper/hots.db`` on macOS).

    The DB used to live next to the repo at ``data/hots.db`` and was
    committed to git so squad members shared it via ``git pull``. Now
    that cloud sync handles cross-machine sharing, the DB is per-user
    state and doesn't belong in source control.
    """
    # Imported lazily to avoid a circular import (config -> db -> config).
    from ..config import default_db_path as _config_db_path

    return _config_db_path()


_PLAYER_MATCH_COLUMNS = [
    "replay_id",
    "slot",
    "toon_handle",
    "display_name",
    "hero",
    "hero_id",
    "skin",
    "banner",
    "team",
    "result",
    "kills",
    "deaths",
    "assists",
    "takedowns",
    "solo_kills",
    "level",
    "hero_damage",
    "siege_damage",
    "structure_damage",
    "creep_damage",
    "minion_damage",
    "minion_kills",
    "summon_damage",
    "physical_damage",
    "spell_damage",
    "healing",
    "self_healing",
    "damage_taken",
    "damage_soaked",
    "teamfight_hero_damage",
    "teamfight_healing",
    "teamfight_damage_taken",
    "teamfight_escapes",
    "experience_contribution",
    "time_spent_dead",
    "time_on_point",
    "merc_camp_captures",
    "watch_tower_captures",
    "regen_globes",
    "town_kills",
    "meta_experience",
    "time_cc_enemy_heroes",
    "time_stunning_enemy_heroes",
    "time_rooting_enemy_heroes",
    "time_silencing_enemy_heroes",
    "highest_kill_streak",
    "multikill",
    "escapes_performed",
    "vengeances_performed",
    "outnumbered_deaths",
    "clutch_heals",
    "protection_given_to_allies",
    "on_fire_time",
    "talents",
    "awards",
    "hero_mastery_tiers",
]


class Store:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Fold 繁體/alias hero spellings to their zh-CN canonical form
        # inside SQL, so ``GROUP BY canon_hero(pm.hero)`` merges a
        # TW/KR-localised replay's "維拉" with "维拉" into one row (and
        # the aggregates re-compute correctly over the merged group).
        # ``deterministic=True`` lets SQLite use it in indexes/GROUP BY.
        self.conn.create_function(
            "canon_hero", 1, _canon_hero_sql, deterministic=True
        )
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._init_schema()
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def drop_read_snapshot(self) -> None:
        """End any pending implicit read transaction.

        Python's sqlite3 module starts an implicit transaction on the
        first ``SELECT`` and holds the read snapshot until the next
        ``commit()`` or ``rollback()``. When another thread (the
        watcher / cloud sync) writes new rows on the *same* connection
        in between, the holder of the open read txn keeps seeing the
        old snapshot until it commits. Call this at every read
        "session boundary" — e.g. when the BP popup runs a fresh
        analysis pass — so the next ``SELECT`` sees the latest
        committed data.

        Cheap on a clean connection (just a COMMIT-with-no-changes), so
        the popup can call it unconditionally per refresh.
        """
        with self._lock:
            try:
                self.conn.commit()
            except Exception:
                # Best-effort — if the connection is mid-transaction
                # in some unusual state, rollback restores us to a
                # known-good snapshot for the next read.
                try:
                    self.conn.rollback()
                except Exception:
                    pass

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _init_schema(self) -> None:
        """Create tables/indexes on a fresh DB.

        On an older DB missing columns referenced by schema.sql's indexes,
        ``executescript`` would abort partway. We catch and let ``_migrate``
        (which runs right after) bring the schema up to date.
        """
        # schema.sql is authored as UTF-8. On Windows, Python defaults
        # text-mode opens to the system code page (GBK on zh-CN locales),
        # which chokes on any non-ASCII byte in the file (e.g. an em-dash
        # inside a SQL comment) with "gbk codec can't decode byte 0x94".
        # Pin the encoding so it works the same on every host.
        with _SCHEMA_PATH.open(encoding="utf-8") as f:
            script = f.read()
        try:
            self.conn.executescript(script)
        except sqlite3.OperationalError:
            # Old DB — execute statement-by-statement so we apply whatever
            # DDL works and skip the rest. Remaining gaps will be fixed by
            # _migrate.
            for stmt in filter(None, (s.strip() for s in script.split(";"))):
                try:
                    self.conn.execute(stmt)
                except sqlite3.OperationalError:
                    continue
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns that may be missing from older DBs."""

        def _cols(table: str) -> set[str]:
            return {
                r["name"]
                for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }

        pm_cols = _cols("player_match")
        want = {
            "hero_id": "TEXT NOT NULL DEFAULT ''",
            "skin": "TEXT NOT NULL DEFAULT ''",
            "banner": "TEXT NOT NULL DEFAULT ''",
            "creep_damage": "INTEGER NOT NULL DEFAULT 0",
            "minion_damage": "INTEGER NOT NULL DEFAULT 0",
            "minion_kills": "INTEGER NOT NULL DEFAULT 0",
            "summon_damage": "INTEGER NOT NULL DEFAULT 0",
            "physical_damage": "INTEGER NOT NULL DEFAULT 0",
            "spell_damage": "INTEGER NOT NULL DEFAULT 0",
            "damage_soaked": "INTEGER NOT NULL DEFAULT 0",
            "teamfight_hero_damage": "INTEGER NOT NULL DEFAULT 0",
            "teamfight_healing": "INTEGER NOT NULL DEFAULT 0",
            "teamfight_damage_taken": "INTEGER NOT NULL DEFAULT 0",
            "teamfight_escapes": "INTEGER NOT NULL DEFAULT 0",
            "time_on_point": "INTEGER NOT NULL DEFAULT 0",
            "watch_tower_captures": "INTEGER NOT NULL DEFAULT 0",
            "regen_globes": "INTEGER NOT NULL DEFAULT 0",
            "town_kills": "INTEGER NOT NULL DEFAULT 0",
            "meta_experience": "INTEGER NOT NULL DEFAULT 0",
            "time_cc_enemy_heroes": "INTEGER NOT NULL DEFAULT 0",
            "time_stunning_enemy_heroes": "INTEGER NOT NULL DEFAULT 0",
            "time_rooting_enemy_heroes": "INTEGER NOT NULL DEFAULT 0",
            "time_silencing_enemy_heroes": "INTEGER NOT NULL DEFAULT 0",
            "highest_kill_streak": "INTEGER NOT NULL DEFAULT 0",
            "multikill": "INTEGER NOT NULL DEFAULT 0",
            "escapes_performed": "INTEGER NOT NULL DEFAULT 0",
            "vengeances_performed": "INTEGER NOT NULL DEFAULT 0",
            "outnumbered_deaths": "INTEGER NOT NULL DEFAULT 0",
            "clutch_heals": "INTEGER NOT NULL DEFAULT 0",
            "protection_given_to_allies": "INTEGER NOT NULL DEFAULT 0",
            "on_fire_time": "INTEGER NOT NULL DEFAULT 0",
            "talents": "TEXT NOT NULL DEFAULT ''",
            "awards": "TEXT NOT NULL DEFAULT ''",
            "hero_mastery_tiers": "TEXT NOT NULL DEFAULT ''",
        }
        for col, decl in want.items():
            if col not in pm_cols:
                self.conn.execute(f"ALTER TABLE player_match ADD COLUMN {col} {decl}")

        rp_cols = _cols("replays")
        if "bans_team0" not in rp_cols:
            self.conn.execute(
                "ALTER TABLE replays ADD COLUMN bans_team0 TEXT NOT NULL DEFAULT ''"
            )
        if "bans_team1" not in rp_cols:
            self.conn.execute(
                "ALTER TABLE replays ADD COLUMN bans_team1 TEXT NOT NULL DEFAULT ''"
            )
        if "match_key" not in rp_cols:
            self.conn.execute(
                "ALTER TABLE replays ADD COLUMN match_key TEXT NOT NULL DEFAULT ''"
            )
        if "random_seed" not in rp_cols:
            self.conn.execute(
                "ALTER TABLE replays ADD COLUMN random_seed INTEGER NOT NULL DEFAULT 0"
            )
        # Unique index on match_key must be created after the column exists.
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_replays_match_key ON replays(match_key) "
            "WHERE match_key != ''"
        )

        # scan_index was added later; older DBs from before incremental
        # scan support need the table created here. Local-only — never
        # synced (see schema.sql for rationale).
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_index (
                path         TEXT PRIMARY KEY,
                mtime_ns     INTEGER NOT NULL,
                size         INTEGER NOT NULL,
                file_hash    TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    # --- ingest ---------------------------------------------------------------

    def has_replay(self, file_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM replays WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return row is not None

    # --- scan_index (local-only, not synced) ---------------------------------

    def scan_index_has(self, path: str, mtime_ns: int, size: int) -> bool:
        """Return True if we've already inspected this exact file revision.

        The fingerprint is ``(path, mtime_ns, size)`` — weaker than a full
        SHA-256 (a user who renames or re-saves a file will defeat it),
        but the upsert in :meth:`upsert_replay` is idempotent on
        ``file_hash`` / ``match_key`` so a false-negative here just means
        one extra parse, never a stats corruption.
        """
        row = self.conn.execute(
            "SELECT 1 FROM scan_index WHERE path = ? AND mtime_ns = ? AND size = ? LIMIT 1",
            (path, mtime_ns, size),
        ).fetchone()
        return row is not None

    def scan_index_touch(
        self, path: str, mtime_ns: int, size: int, file_hash: str, seen_at: str
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO scan_index (path, mtime_ns, size, file_hash, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime_ns = excluded.mtime_ns,
                    size = excluded.size,
                    file_hash = excluded.file_hash,
                    last_seen_at = excluded.last_seen_at
                """,
                (path, mtime_ns, size, file_hash, seen_at),
            )
            self.conn.commit()

    def upsert_replay(self, replay: Replay) -> tuple[int, bool]:
        """Return ``(replay_id, inserted)``.

        Idempotent on two keys:
        - ``file_hash``: same file already ingested → return existing row.
        - ``match_key``: another perspective of the same match already in DB →
          return that existing row, don't create duplicates. This is what
          lets us import replays collected from teammates without inflating
          stats.
        """
        with self._lock:
            existing = self.conn.execute(
                "SELECT id FROM replays WHERE file_hash = ?", (replay.file_hash,)
            ).fetchone()
            if existing is not None:
                return int(existing["id"]), False

            if replay.match_key:
                existing_match = self.conn.execute(
                    "SELECT id FROM replays WHERE match_key = ?", (replay.match_key,)
                ).fetchone()
                if existing_match is not None:
                    return int(existing_match["id"]), False

            cur = self.conn.execute(
                """
                INSERT INTO replays
                    (file_hash, match_key, random_seed, file_path,
                     map_name, mode, build, protocol_build,
                     played_at, duration_s, winner_team, bans_team0, bans_team1)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    replay.file_hash,
                    replay.match_key,
                    replay.random_seed,
                    replay.file_path,
                    replay.map_name,
                    replay.mode,
                    replay.build,
                    replay.protocol_build,
                    replay.played_at.isoformat(),
                    replay.duration_seconds,
                    replay.winner_team,
                    ",".join(replay.bans_team0),
                    ",".join(replay.bans_team1),
                ),
            )
            replay_id = int(cur.lastrowid)

            for p in replay.players:
                self._upsert_player(p, replay.played_at.isoformat())
                self._insert_player_match(replay_id, p)

            self.conn.commit()
            return replay_id, True

    def _upsert_player(self, p: PlayerMatch, seen_at: str) -> None:
        self.conn.execute(
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
            (p.toon_handle, p.name, seen_at),
        )

    def _insert_player_match(self, replay_id: int, p: PlayerMatch) -> None:
        values = {
            "replay_id": replay_id,
            "slot": p.slot,
            "toon_handle": p.toon_handle,
            "display_name": p.name,
            "hero": p.hero,
            "hero_id": p.hero_id,
            "skin": p.skin,
            "banner": p.banner,
            "team": p.team,
            "result": p.result,
            "kills": p.kills,
            "deaths": p.deaths,
            "assists": p.assists,
            "takedowns": p.takedowns,
            "solo_kills": p.solo_kills,
            "level": p.level,
            "hero_damage": p.hero_damage,
            "siege_damage": p.siege_damage,
            "structure_damage": p.structure_damage,
            "creep_damage": p.creep_damage,
            "minion_damage": p.minion_damage,
            "minion_kills": p.minion_kills,
            "summon_damage": p.summon_damage,
            "physical_damage": p.physical_damage,
            "spell_damage": p.spell_damage,
            "healing": p.healing,
            "self_healing": p.self_healing,
            "damage_taken": p.damage_taken,
            "damage_soaked": p.damage_soaked,
            "teamfight_hero_damage": p.teamfight_hero_damage,
            "teamfight_healing": p.teamfight_healing,
            "teamfight_damage_taken": p.teamfight_damage_taken,
            "teamfight_escapes": p.teamfight_escapes,
            "experience_contribution": p.experience_contribution,
            "time_spent_dead": p.time_spent_dead,
            "time_on_point": p.time_on_point,
            "merc_camp_captures": p.merc_camp_captures,
            "watch_tower_captures": p.watch_tower_captures,
            "regen_globes": p.regen_globes,
            "town_kills": p.town_kills,
            "meta_experience": p.meta_experience,
            "time_cc_enemy_heroes": p.time_cc_enemy_heroes,
            "time_stunning_enemy_heroes": p.time_stunning_enemy_heroes,
            "time_rooting_enemy_heroes": p.time_rooting_enemy_heroes,
            "time_silencing_enemy_heroes": p.time_silencing_enemy_heroes,
            "highest_kill_streak": p.highest_kill_streak,
            "multikill": p.multikill,
            "escapes_performed": p.escapes_performed,
            "vengeances_performed": p.vengeances_performed,
            "outnumbered_deaths": p.outnumbered_deaths,
            "clutch_heals": p.clutch_heals,
            "protection_given_to_allies": p.protection_given_to_allies,
            "on_fire_time": p.on_fire_time,
            "talents": json.dumps(p.talents, ensure_ascii=False),
            "awards": json.dumps(p.awards, ensure_ascii=False),
            "hero_mastery_tiers": json.dumps(p.hero_mastery_tiers, ensure_ascii=False),
        }
        cols = list(values.keys())
        placeholders = ",".join("?" for _ in cols)
        self.conn.execute(
            f"INSERT OR REPLACE INTO player_match ({','.join(cols)}) VALUES ({placeholders})",
            tuple(values[c] for c in cols),
        )

    # --- queries --------------------------------------------------------------

    def count_replays(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM replays").fetchone()[0])

    def count_players(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM players").fetchone()[0])

    def find_players_by_name(self, name: str) -> list[sqlite3.Row]:
        """Exact match → case-insensitive → fuzzy (small edit distance).

        Fuzzy fallback handles OCR near-misses for both:
          - ASCII: ``jeanshong`` vs ``jeanshang`` (a/o glyph confusion)
          - CJK:   ``星海大恶魔`` vs ``星海大悪魔`` (one Han variant)

        For CJK we cap edit distance at 1 because two-character drift on a
        4-6 char Chinese name has too many false-positive collisions
        (different real players sometimes share a 2-char substring).
        """
        rows = self.conn.execute(
            "SELECT toon_handle, display_name, last_seen_at FROM players WHERE display_name = ?",
            (name,),
        ).fetchall()
        if rows:
            return rows
        rows = self.conn.execute(
            "SELECT toon_handle, display_name, last_seen_at FROM players "
            "WHERE display_name = ? COLLATE NOCASE",
            (name,),
        ).fetchall()
        if rows:
            return rows
        if len(name) < 3:
            return []

        is_ascii = name.isascii()
        if is_ascii:
            max_dist = 1 if len(name) <= 6 else 2
        else:
            # CJK: only allow a single-character substitution to avoid
            # collapsing "李敏" / "李明" / "李莉" together.
            max_dist = 1 if len(name) >= 4 else 0
            if max_dist == 0:
                return []

        all_players = self.conn.execute(
            "SELECT toon_handle, display_name, last_seen_at FROM players"
        ).fetchall()
        candidates: list[tuple[int, sqlite3.Row]] = []
        for r in all_players:
            other = r["display_name"]
            if abs(len(other) - len(name)) > max_dist:
                continue
            # ASCII-only matching for ASCII queries to avoid noise; mixed
            # queries (Chinese + Latin) we treat case-sensitive.
            if is_ascii != other.isascii():
                continue
            if is_ascii:
                d = _levenshtein(name.lower(), other.lower())
            else:
                d = _levenshtein(name, other)
            if d <= max_dist:
                candidates.append((d, r))
        candidates.sort(key=lambda c: c[0])
        return [r for _, r in candidates]

    def matches_for_handle(
        self,
        toon_handle: str,
        limit: int = 50,
        *,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT pm.*, r.map_name, r.mode, r.played_at, r.duration_s, r.winner_team,
                   r.bans_team0, r.bans_team1
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE pm.toon_handle = ?{clause}
            ORDER BY r.played_at DESC
            LIMIT ?
            """,
            (toon_handle, *mode_params, limit),
        ).fetchall()

    def hero_stats_for_handle(
        self,
        toon_handle: str,
        *,
        map_name: str | None = None,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        clause, mode_params = _mode_clause(mode_filter)
        sql = f"""
            SELECT
                canon_hero(pm.hero)             AS hero,
                MAX(pm.hero_id)                 AS hero_id,
                COUNT(*) AS games,
                SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                AVG(pm.kills)                   AS avg_k,
                AVG(pm.deaths)                  AS avg_d,
                AVG(pm.assists)                 AS avg_a,
                AVG(pm.hero_damage)             AS avg_hero_dmg,
                AVG(pm.siege_damage)            AS avg_siege_dmg,
                AVG(pm.structure_damage)        AS avg_structure_dmg,
                AVG(pm.healing)                 AS avg_healing,
                AVG(pm.damage_taken)            AS avg_dmg_taken,
                AVG(pm.experience_contribution) AS avg_xp,
                AVG(pm.time_cc_enemy_heroes)    AS avg_cc,
                AVG(pm.highest_kill_streak)     AS avg_streak,
                MAX(r.played_at)                AS last_played
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE pm.toon_handle = ?{clause}
        """
        params: list[Any] = [toon_handle, *mode_params]
        if map_name:
            sql += " AND r.map_name = ?"
            params.append(map_name)
        sql += " GROUP BY canon_hero(pm.hero) ORDER BY games DESC, last_played DESC"
        return self.conn.execute(sql, params).fetchall()

    def overall_for_handle(
        self,
        toon_handle: str,
        *,
        map_name: str | None = None,
        since_iso: str | None = None,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> sqlite3.Row | None:
        clause, mode_params = _mode_clause(mode_filter)
        sql = f"""
            SELECT
                COUNT(*) AS games,
                SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                AVG(pm.kills)   AS avg_k,
                AVG(pm.deaths)  AS avg_d,
                AVG(pm.assists) AS avg_a,
                AVG(pm.hero_damage) AS avg_hero_dmg,
                AVG(pm.siege_damage) AS avg_siege_dmg,
                AVG(pm.structure_damage) AS avg_structure_dmg,
                AVG(pm.damage_taken) AS avg_dmg_taken,
                AVG(pm.healing) AS avg_healing,
                AVG(pm.self_healing) AS avg_self_healing,
                AVG(pm.experience_contribution) AS avg_xp,
                AVG(pm.time_cc_enemy_heroes) AS avg_cc,
                AVG(pm.merc_camp_captures) AS avg_merc,
                MAX(r.played_at) AS last_played
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE pm.toon_handle = ?{clause}
        """
        params: list[Any] = [toon_handle, *mode_params]
        if map_name:
            sql += " AND r.map_name = ?"
            params.append(map_name)
        if since_iso:
            sql += " AND r.played_at >= ?"
            params.append(since_iso)
        return self.conn.execute(sql, params).fetchone()

    def played_with_for_handle(
        self,
        toon_handle: str,
        *,
        teammate: bool = True,
        limit: int = 10,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        """Players this handle has been matched with (teammate or opponent)."""
        op = "=" if teammate else "!="
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT
                other.toon_handle,
                other.display_name,
                COUNT(*) AS games,
                SUM(CASE WHEN other.team {op} me.team AND other.result = me.result THEN 1 ELSE 0 END)
                    AS shared_wins
            FROM player_match me
            JOIN player_match other
              ON other.replay_id = me.replay_id
             AND other.toon_handle != me.toon_handle
             AND other.team {op} me.team
            JOIN replays r ON r.id = me.replay_id
            WHERE me.toon_handle = ?{clause}
            GROUP BY other.toon_handle, other.display_name
            ORDER BY games DESC
            LIMIT ?
            """,
            (toon_handle, *mode_params, limit),
        ).fetchall()

    # --- player rankings ----------------------------------------------------

    def player_rankings_seen(
        self,
        squad_handles: tuple[str, ...],
        *,
        min_games: int = 5,
        limit: int = 500,
        hero: str | None = None,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        """Per-player aggregate over every match the squad played in.

        No team-side filter — the row counts cover both ally and enemy
        appearances, so ``games`` is "how many of our matches you
        showed up in" and ``wins`` is "how many of those YOU won
        (your team won, regardless of whether that was our team)".
        Use this when the leaderboard doesn't care about side.

        ``hero`` restricts the aggregate to games where the player
        was on that specific hero. Useful for "show me the top
        Greymane players we've met" — without it the average is
        across whatever hero pool the player happened to flex.
        """
        if not squad_handles:
            return []

        clause, mode_params = _mode_clause(mode_filter)
        squad_placeholders = ",".join("?" for _ in squad_handles)
        hero_clause = ""
        hero_params: list[Any] = []
        if hero:
            hero_clause = " AND canon_hero(pm.hero) = canon_hero(?)"
            hero_params = [hero]
        return self.conn.execute(
            f"""
            SELECT pm.toon_handle,
                   COALESCE(p.display_name, MAX(pm.display_name)) AS display_name,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(pm.kills)            AS avg_k,
                   AVG(pm.deaths)           AS avg_d,
                   AVG(pm.assists)          AS avg_a,
                   AVG(pm.hero_damage)      AS avg_hero_dmg,
                   {_avg_when("siege_damage", alias="avg_siege_dmg")},
                   {_avg_when("structure_damage", alias="avg_structure_dmg")},
                   {_avg_when("healing", alias="avg_healing")},
                   {_avg_when("damage_taken", alias="avg_dmg_taken")},
                   {_avg_when("damage_soaked", alias="avg_dmg_soaked")},
                   AVG(pm.experience_contribution) AS avg_xp,
                   {_avg_when("time_cc_enemy_heroes", alias="avg_cc")},
                   MAX(r.played_at)         AS last_seen_at
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            LEFT JOIN players p ON p.toon_handle = pm.toon_handle
            WHERE pm.replay_id IN (
                SELECT replay_id
                FROM player_match
                WHERE toon_handle IN ({squad_placeholders})
            )
            {clause}
            {hero_clause}
            GROUP BY pm.toon_handle
            HAVING COUNT(*) >= ?
            ORDER BY games DESC
            LIMIT ?
            """,
            (
                *squad_handles,
                *mode_params,
                *hero_params,
                min_games,
                limit,
            ),
        ).fetchall()

    def player_rankings_all(
        self,
        *,
        min_games: int = 5,
        limit: int = 10_000,
        hero: str | None = None,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        """Per-player aggregate over **every** player in the DB.

        Unlike :meth:`player_rankings_seen`, this is NOT scoped to games
        the squad played in — it's "everyone who has logged enough games
        in this mode", so the leaderboard population is stable regardless
        of who the viewer marks as their squad. The squad selection only
        drives row highlighting upstream, never membership.

        ``hero`` restricts the aggregate to games on that hero; folds
        繁體/alias spellings so a TW-localised row still matches.
        """
        clause, mode_params = _mode_clause(mode_filter)
        hero_clause = ""
        hero_params: list[Any] = []
        if hero:
            hero_clause = " AND canon_hero(pm.hero) = canon_hero(?)"
            hero_params = [hero]
        return self.conn.execute(
            f"""
            SELECT pm.toon_handle,
                   COALESCE(p.display_name, MAX(pm.display_name)) AS display_name,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(pm.kills)            AS avg_k,
                   AVG(pm.deaths)           AS avg_d,
                   AVG(pm.assists)          AS avg_a,
                   AVG(pm.hero_damage)      AS avg_hero_dmg,
                   {_avg_when("siege_damage", alias="avg_siege_dmg")},
                   {_avg_when("structure_damage", alias="avg_structure_dmg")},
                   {_avg_when("healing", alias="avg_healing")},
                   {_avg_when("damage_taken", alias="avg_dmg_taken")},
                   {_avg_when("damage_soaked", alias="avg_dmg_soaked")},
                   AVG(pm.experience_contribution) AS avg_xp,
                   {_avg_when("time_cc_enemy_heroes", alias="avg_cc")},
                   MAX(r.played_at)         AS last_seen_at
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            LEFT JOIN players p ON p.toon_handle = pm.toon_handle
            WHERE 1=1{clause}{hero_clause}
            GROUP BY pm.toon_handle
            HAVING COUNT(*) >= ?
            ORDER BY games DESC
            LIMIT ?
            """,
            (*mode_params, *hero_params, min_games, limit),
        ).fetchall()

    def player_rankings_vs_squad(
        self,
        squad_handles: tuple[str, ...],
        *,
        side: str = "teammate",
        min_games: int = 5,
        limit: int = 200,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        """Per-player aggregate restricted to games involving the squad.

        ``side="teammate"`` → only count games where this player was on
        the *same* team as the squad ("how often did they win for us?").
        ``side="opponent"`` → games where they were on the *other* team
        ("how often did they beat us?").

        Each row's ``games`` and ``wins`` are still per-match counts;
        ``result = 1`` already means "this player's team won", which is
        the right framing for both boards (they win with us → we win
        together; they win against us → they beat us).

        Squad members themselves are excluded so the boards are "people
        we've encountered" rather than "us + everyone else".
        """
        if side not in ("teammate", "opponent"):
            raise ValueError(f"unknown side: {side!r}")
        if not squad_handles:
            return []

        clause, mode_params = _mode_clause(mode_filter)
        squad_placeholders = ",".join("?" for _ in squad_handles)
        team_op = "=" if side == "teammate" else "!="
        # We use MIN(team) as a proxy for "the team the squad was on
        # in this match". A 5-stack always queues onto a single team,
        # so MIN == MAX in practice. If a squad member ever ends up on
        # the opposite team via solo-queue, MIN picks one side and the
        # others count as opponents — acceptable noise in a feature
        # that's already best-effort.
        # Squad members are *not* excluded from the result. The boards
        # aggregate "everyone who's played alongside or against the
        # squad", and the squad themselves are part of "alongside the
        # squad" — including them lets the user see their own stats
        # ranked next to random teammates and call out who in the
        # 5-stack is dragging the team down.
        return self.conn.execute(
            f"""
            SELECT pm.toon_handle,
                   COALESCE(p.display_name, MAX(pm.display_name)) AS display_name,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(pm.kills)            AS avg_k,
                   AVG(pm.deaths)           AS avg_d,
                   AVG(pm.assists)          AS avg_a,
                   AVG(pm.hero_damage)      AS avg_hero_dmg,
                   {_avg_when("siege_damage", alias="avg_siege_dmg")},
                   {_avg_when("structure_damage", alias="avg_structure_dmg")},
                   {_avg_when("healing", alias="avg_healing")},
                   {_avg_when("damage_taken", alias="avg_dmg_taken")},
                   {_avg_when("damage_soaked", alias="avg_dmg_soaked")},
                   AVG(pm.experience_contribution) AS avg_xp,
                   {_avg_when("time_cc_enemy_heroes", alias="avg_cc")},
                   MAX(r.played_at)         AS last_seen_at
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            JOIN (
                SELECT replay_id, MIN(team) AS our_team
                FROM player_match
                WHERE toon_handle IN ({squad_placeholders})
                GROUP BY replay_id
            ) otm ON otm.replay_id = pm.replay_id
            LEFT JOIN players p ON p.toon_handle = pm.toon_handle
            WHERE pm.team {team_op} otm.our_team
              {clause}
            GROUP BY pm.toon_handle
            HAVING COUNT(*) >= ?
            ORDER BY games DESC
            LIMIT ?
            """,
            (
                *squad_handles,
                *mode_params,
                min_games,
                limit,
            ),
        ).fetchall()

    def squad_handles(
        self,
        *,
        min_games: int = 20,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> set[str]:
        """Heuristic: handles that show up *very often* in our DB are us.

        We don't store an explicit "this is our squad" list anywhere — the
        DB is just every replay we've ever ingested. But the squad has
        played hundreds of games together, and random opponents almost
        never reappear. So the top of the play-frequency distribution is
        the squad. Used to filter ourselves out of the kingmaker /
        boogeyman boards.
        """
        clause, mode_params = _mode_clause(mode_filter)
        rows = self.conn.execute(
            f"""
            SELECT pm.toon_handle, COUNT(*) AS games
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE 1=1{clause}
            GROUP BY pm.toon_handle
            HAVING games >= ?
            """,
            (*mode_params, min_games),
        ).fetchall()
        return {r["toon_handle"] for r in rows}

    def squad_candidates(
        self,
        *,
        min_games: int = 10,
        limit: int = 60,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[dict]:
        """Frequent players, most-played first, for the squad-picker UI.

        Returns handle + display name + game count so the user can pick
        who counts as "their squad". A looser ``min_games`` than
        :meth:`squad_handles` so the picker surfaces everyone plausibly
        on the roster, not just the auto-detected core.
        """
        clause, mode_params = _mode_clause(mode_filter)
        rows = self.conn.execute(
            f"""
            SELECT pm.toon_handle,
                   MAX(pm.display_name) AS display_name,
                   COUNT(*) AS games
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE 1=1{clause}
            GROUP BY pm.toon_handle
            HAVING games >= ?
            ORDER BY games DESC
            LIMIT ?
            """,
            (*mode_params, min_games, limit),
        ).fetchall()
        return [
            {
                "toon_handle": r["toon_handle"],
                "display_name": r["display_name"] or r["toon_handle"],
                "games": int(r["games"]),
            }
            for r in rows
        ]

    def side_split_vs_squad(
        self,
        toon_handle: str,
        squad_handles: tuple[str, ...],
        *,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> dict[str, int]:
        """How many shared matches landed this handle on our team vs the
        opposite team, plus the wins on each side.

        ``ally`` / ``ally_wins`` count games where the player was on the
        squad's team (typically pugs queued with us). ``enemy`` /
        ``enemy_wins`` count games where they queued against us. Returns
        zeros for an empty squad list so callers can short-circuit.
        """
        if not squad_handles:
            return {"ally": 0, "ally_wins": 0, "enemy": 0, "enemy_wins": 0}
        clause, mode_params = _mode_clause(mode_filter)
        squad_placeholders = ",".join("?" for _ in squad_handles)
        # MIN(team) is "the team the squad sat on" — same proxy used by
        # player_rankings_vs_squad. A 5-stack always queues onto a
        # single team so MIN==MAX in practice.
        row = self.conn.execute(
            f"""
            SELECT
              SUM(CASE WHEN pm.team = otm.our_team THEN 1 ELSE 0 END)
                AS ally,
              SUM(CASE WHEN pm.team = otm.our_team AND pm.result = 1 THEN 1 ELSE 0 END)
                AS ally_wins,
              SUM(CASE WHEN pm.team != otm.our_team THEN 1 ELSE 0 END)
                AS enemy,
              SUM(CASE WHEN pm.team != otm.our_team AND pm.result = 1 THEN 1 ELSE 0 END)
                AS enemy_wins
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            JOIN (
                SELECT replay_id, MIN(team) AS our_team
                FROM player_match
                WHERE toon_handle IN ({squad_placeholders})
                GROUP BY replay_id
            ) otm ON otm.replay_id = pm.replay_id
            WHERE pm.toon_handle = ?{clause}
            """,
            (*squad_handles, toon_handle, *mode_params),
        ).fetchone()
        if row is None:
            return {"ally": 0, "ally_wins": 0, "enemy": 0, "enemy_wins": 0}
        return {
            "ally": int(row["ally"] or 0),
            "ally_wins": int(row["ally_wins"] or 0),
            "enemy": int(row["enemy"] or 0),
            "enemy_wins": int(row["enemy_wins"] or 0),
        }

    def per_match_metric_samples(
        self,
        *,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
        limit: int = 100_000,
    ) -> list[sqlite3.Row]:
        """Raw per-match metric rows used as the population baseline
        for the composite "combat power" score.

        Each row = one player_match. Computing percentile ranks against
        this universe (rather than each leaderboard slice individually)
        keeps the score stable: a 22k-hero-damage average is "low" no
        matter which board you put the player on, instead of being
        rescaled to 100 just because they're the only entry on a
        sparsely-populated worst-teammate board.
        """
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT pm.kills, pm.deaths, pm.assists,
                   pm.hero_damage, pm.siege_damage, pm.structure_damage,
                   pm.healing, pm.damage_taken, pm.damage_soaked,
                   pm.experience_contribution AS xp,
                   pm.time_cc_enemy_heroes    AS cc,
                   pm.result
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE 1=1{clause}
            ORDER BY r.played_at DESC
            LIMIT ?
            """,
            (*mode_params, limit),
        ).fetchall()

    def hero_aggregate_stats(
        self,
        *,
        map_name: str | None = None,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        """Per-hero average stats — the population the hero leaderboard
        scores against."""
        clause, mode_params = _mode_clause(mode_filter)
        sql = f"""
            SELECT canon_hero(pm.hero) AS hero,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(pm.kills)            AS avg_k,
                   AVG(pm.deaths)           AS avg_d,
                   AVG(pm.assists)          AS avg_a,
                   AVG(pm.hero_damage)      AS avg_hero_dmg,
                   {_avg_when("siege_damage", alias="avg_siege_dmg")},
                   {_avg_when("structure_damage", alias="avg_structure_dmg")},
                   {_avg_when("healing", alias="avg_healing")},
                   {_avg_when("damage_taken", alias="avg_dmg_taken")},
                   {_avg_when("damage_soaked", alias="avg_dmg_soaked")},
                   AVG(pm.experience_contribution) AS avg_xp,
                   {_avg_when("time_cc_enemy_heroes", alias="avg_cc")}
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE 1=1{clause}
        """
        params: list[Any] = list(mode_params)
        if map_name:
            sql += " AND r.map_name = ?"
            params.append(map_name)
        sql += " GROUP BY canon_hero(pm.hero)"
        return self.conn.execute(sql, params).fetchall()

    # --- hero-centric analytics ---------------------------------------------

    def all_heroes(
        self, *, mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER
    ) -> list[sqlite3.Row]:
        """List heroes present in the DB with total games and wins."""
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT canon_hero(pm.hero) AS hero,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(pm.hero_damage) AS avg_hero_dmg,
                   AVG(pm.siege_damage) AS avg_siege_dmg,
                   AVG(pm.healing) AS avg_healing,
                   AVG(pm.kills) AS avg_k,
                   AVG(pm.deaths) AS avg_d,
                   AVG(pm.assists) AS avg_a
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE 1=1{clause}
            GROUP BY canon_hero(pm.hero)
            ORDER BY games DESC, wins DESC
            """,
            mode_params,
        ).fetchall()

    def hero_by_map(
        self, hero: str, *, mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER
    ) -> list[sqlite3.Row]:
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT r.map_name,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE canon_hero(pm.hero) = canon_hero(?){clause}
            GROUP BY r.map_name
            ORDER BY games DESC
            """,
            (hero, *mode_params),
        ).fetchall()

    def map_hero_winrates(
        self,
        map_name: str,
        *,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        """All heroes played on this map, with raw games/wins."""
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT canon_hero(pm.hero) AS hero,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE r.map_name = ?{clause}
            GROUP BY canon_hero(pm.hero)
            ORDER BY games DESC
            """,
            (map_name, *mode_params),
        ).fetchall()

    def global_hero_winrate(
        self, hero: str, *, mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER
    ) -> tuple[int, int]:
        """Return ``(games, wins)`` across all maps."""
        clause, mode_params = _mode_clause(mode_filter)
        row = self.conn.execute(
            f"""
            SELECT COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE canon_hero(pm.hero) = canon_hero(?){clause}
            """,
            (hero, *mode_params),
        ).fetchone()
        if not row:
            return 0, 0
        return int(row["games"] or 0), int(row["wins"] or 0)

    def hero_talents(
        self,
        hero: str,
        *,
        map_name: str | None = None,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        """Every (tier_index, talent_name, games, wins) row for this hero.

        We decode the JSON ``talents`` column per match in Python because
        SQLite lacks built-in JSON array indexing across versions.
        """
        import json

        clause, mode_params = _mode_clause(mode_filter)
        params: list[Any] = [hero, *mode_params]
        sql = f"""
            SELECT pm.talents, pm.result
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE canon_hero(pm.hero) = canon_hero(?){clause}
        """
        if map_name:
            sql += " AND r.map_name = ?"
            params.append(map_name)

        counts: dict[tuple[int, str], dict[str, int]] = {}
        for row in self.conn.execute(sql, params).fetchall():
            try:
                talents = json.loads(row["talents"] or "[]")
            except Exception:
                continue
            for tier_idx, talent in enumerate(talents, start=1):
                key = (tier_idx, str(talent))
                c = counts.setdefault(key, {"games": 0, "wins": 0})
                c["games"] += 1
                if row["result"] == 1:
                    c["wins"] += 1

        # Materialize as a flat list of Row-like dicts.
        out = [
            {"tier": k[0], "talent": k[1], "games": v["games"], "wins": v["wins"]}
            for k, v in counts.items()
        ]
        out.sort(key=lambda r: (r["tier"], -r["games"], -r["wins"]))
        return out  # type: ignore[return-value]

    def hero_overall(
        self, hero: str, *, mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER
    ) -> sqlite3.Row | None:
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(pm.kills)   AS avg_k,
                   AVG(pm.deaths)  AS avg_d,
                   AVG(pm.assists) AS avg_a,
                   AVG(pm.hero_damage) AS avg_hero_dmg,
                   AVG(pm.siege_damage) AS avg_siege_dmg,
                   AVG(pm.healing) AS avg_healing
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE canon_hero(pm.hero) = canon_hero(?){clause}
            """,
            (hero, *mode_params),
        ).fetchone()

    def map_winrate_for_handle(
        self,
        toon_handle: str,
        *,
        mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER,
    ) -> list[sqlite3.Row]:
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT r.map_name,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE pm.toon_handle = ?{clause}
            GROUP BY r.map_name
            ORDER BY games DESC
            """,
            (toon_handle, *mode_params),
        ).fetchall()

    # --- match records (web browser) ----------------------------------------

    def match_detail(
        self, replay_id: int
    ) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
        """Return ``(replay_row, [player_match rows for all slots])``.

        The roster is ordered by ``(team, slot)`` so the caller can split
        it into the two five-player teams without re-sorting. Returns
        ``(None, [])`` when the id is unknown — callers translate that to
        a 404.

        No mode filter: a detail view shows whatever the match actually
        was, including ARAM games.
        """
        replay = self.conn.execute(
            "SELECT * FROM replays WHERE id = ?", (replay_id,)
        ).fetchone()
        if replay is None:
            return None, []
        players = self.conn.execute(
            """
            SELECT pm.*, r.map_name, r.mode, r.played_at, r.duration_s,
                   r.winner_team
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE pm.replay_id = ?
            ORDER BY pm.team, pm.slot
            """,
            (replay_id,),
        ).fetchall()
        return replay, players

    def list_matches(
        self,
        *,
        map_name: str | None = None,
        mode: str | None = "Storm League",
        player: str | None = None,
        result: int | None = None,
        since_iso: str | None = None,
        until_iso: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        """Filtered, paginated replay list ordered by ``played_at`` DESC.

        Returns ``(rows, total_count)`` where ``total_count`` is the match
        count for the same filters without ``LIMIT``/``OFFSET`` so the UI
        can render pagination controls.

        Filters:
        - ``map_name`` / ``mode`` — exact match. Pass ``mode=None`` to
          include every mode (default keeps Storm League, matching the
          rest of the app).
        - ``player`` — a ``toon_handle`` *or* ``display_name``. When set,
          the query joins ``player_match`` so a match counts only if that
          player appeared in it.
        - ``result`` — ``1`` win / ``0`` loss, interpreted from that
          ``player``'s perspective (the DB stores ``1`` win / ``2`` loss,
          so a ``0`` request maps to ``result != 1``). Only meaningful
          together with ``player``; ignored otherwise.
        - ``since_iso`` / ``until_iso`` — half-open ``[since, until)`` on
          ``played_at``.
        """
        where: list[str] = ["1=1"]
        params: list[Any] = []
        join = ""

        if player:
            join = "JOIN player_match pm ON pm.replay_id = r.id"
            where.append("(pm.toon_handle = ? OR pm.display_name = ?)")
            params.extend([player, player])
            if result is not None:
                if result == 1:
                    where.append("pm.result = 1")
                else:
                    where.append("pm.result != 1")

        if map_name:
            where.append("r.map_name = ?")
            params.append(map_name)
        if mode:
            where.append("r.mode = ?")
            params.append(mode)
        if since_iso:
            where.append("r.played_at >= ?")
            params.append(since_iso)
        if until_iso:
            where.append("r.played_at < ?")
            params.append(until_iso)

        where_sql = " AND ".join(where)

        total = int(
            self.conn.execute(
                f"SELECT COUNT(*) FROM replays r {join} WHERE {where_sql}",
                params,
            ).fetchone()[0]
        )

        rows = self.conn.execute(
            f"""
            SELECT r.id, r.match_key, r.map_name, r.mode, r.played_at,
                   r.duration_s, r.winner_team, r.bans_team0, r.bans_team1
            FROM replays r {join}
            WHERE {where_sql}
            ORDER BY r.played_at DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
        return rows, total

    def match_roster_brief(self, replay_ids: list[int]) -> list[sqlite3.Row]:
        """``(replay_id, team, slot, hero, hero_id, display_name, result)``
        for every player in the given replays, in one query.

        Used by the match-list view to render each row's ten heroes
        without an N+1 query per match. Returns ``[]`` for an empty id
        list (an empty ``IN ()`` is a SQL syntax error).
        """
        if not replay_ids:
            return []
        placeholders = ",".join("?" for _ in replay_ids)
        return self.conn.execute(
            f"""
            SELECT replay_id, team, slot, hero, hero_id, display_name, result
            FROM player_match
            WHERE replay_id IN ({placeholders})
            ORDER BY replay_id, team, slot
            """,
            tuple(replay_ids),
        ).fetchall()
