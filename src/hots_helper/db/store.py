"""SQLite store for HotS replay ingest + player lookup."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from ..parser.replay import PlayerMatch, Replay

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Default mode filter applied to every stats query. ARAM games are kept in the
# DB (useful for player-history lookups) but we never use them for BP / winrate
# analysis because hero pool is random and draft mechanics are absent.
DEFAULT_MODE_FILTER: tuple[str, ...] = ("Storm League",)


def _mode_clause(mode_filter: tuple[str, ...] | None, alias: str = "r") -> tuple[str, list[Any]]:
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
    """Project-local default (used by the CLI when no --db is passed).

    The desktop UI uses :func:`hots_helper.config.default_db_path` so that
    packaged builds store data in the user's config dir. The CLI retains
    the project-local default so ``hots scan`` while developing doesn't
    silently write to somewhere unexpected.
    """
    here = Path(__file__).resolve().parents[3]
    return here / "data" / "hots.db"


_PLAYER_MATCH_COLUMNS = [
    "replay_id", "slot", "toon_handle", "display_name", "hero", "hero_id",
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
]


class Store:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._init_schema()
        self._migrate()

    def close(self) -> None:
        self.conn.close()

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
        with _SCHEMA_PATH.open() as f:
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
            self.conn.execute("ALTER TABLE replays ADD COLUMN bans_team0 TEXT NOT NULL DEFAULT ''")
        if "bans_team1" not in rp_cols:
            self.conn.execute("ALTER TABLE replays ADD COLUMN bans_team1 TEXT NOT NULL DEFAULT ''")
        if "match_key" not in rp_cols:
            self.conn.execute("ALTER TABLE replays ADD COLUMN match_key TEXT NOT NULL DEFAULT ''")
        if "random_seed" not in rp_cols:
            self.conn.execute(
                "ALTER TABLE replays ADD COLUMN random_seed INTEGER NOT NULL DEFAULT 0"
            )
        # Unique index on match_key must be created after the column exists.
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_replays_match_key ON replays(match_key) "
            "WHERE match_key != ''"
        )
        self.conn.commit()

    # --- ingest ---------------------------------------------------------------

    def has_replay(self, file_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM replays WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return row is not None

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
        """Exact match first, then case-insensitive, then fuzzy (edit dist ≤ 2).

        Fuzzy fallback handles OCR near-misses like ``jeanshong`` vs
        ``jeanshang`` — common with stylized game fonts where ``a`` and ``o``
        look nearly identical.
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
        if not name.isascii() or len(name) < 4:
            return []
        all_players = self.conn.execute(
            "SELECT toon_handle, display_name, last_seen_at FROM players"
        ).fetchall()
        max_dist = 1 if len(name) <= 6 else 2
        candidates: list[tuple[int, sqlite3.Row]] = []
        for r in all_players:
            other = r["display_name"]
            if not other.isascii() or abs(len(other) - len(name)) > max_dist:
                continue
            d = _levenshtein(name.lower(), other.lower())
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
                pm.hero,
                pm.hero_id,
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
        sql += " GROUP BY pm.hero, pm.hero_id ORDER BY games DESC, last_played DESC"
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

    # --- hero-centric analytics ---------------------------------------------

    def all_heroes(
        self, *, mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER
    ) -> list[sqlite3.Row]:
        """List heroes present in the DB with total games and wins."""
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT pm.hero AS hero,
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
            GROUP BY pm.hero
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
            WHERE pm.hero = ?{clause}
            GROUP BY r.map_name
            ORDER BY games DESC
            """,
            (hero, *mode_params),
        ).fetchall()

    def map_hero_winrates(
        self, map_name: str, *, mode_filter: tuple[str, ...] | None = DEFAULT_MODE_FILTER
    ) -> list[sqlite3.Row]:
        """All heroes played on this map, with raw games/wins."""
        clause, mode_params = _mode_clause(mode_filter)
        return self.conn.execute(
            f"""
            SELECT pm.hero,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE r.map_name = ?{clause}
            GROUP BY pm.hero
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
            WHERE pm.hero = ?{clause}
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
            WHERE pm.hero = ?{clause}
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
            WHERE pm.hero = ?{clause}
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
