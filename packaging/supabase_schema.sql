-- HotS Helper — Supabase schema
--
-- Run this once in the Supabase SQL editor (Database → SQL Editor → New
-- query, paste, click "Run") to create the cloud copy of our local
-- replay store. The columns mirror src/hots_helper/db/schema.sql so the
-- sync layer can copy rows verbatim.
--
-- Recommended setup:
--   1. Create a fresh Supabase project (free tier is fine: 500 MB DB,
--      2 GB egress / month).
--   2. Run this script. It creates 3 tables + a matching set of indexes,
--      then turns ON Row Level Security (RLS) but seeds policies that
--      allow only authenticated requests holding the squad's anon key.
--   3. Take the project URL + anon key from "Project settings → API"
--      and paste them into Settings → Cloud sync inside the app.

-- =============================================================
-- Tables
-- =============================================================

CREATE TABLE IF NOT EXISTS replays (
    -- match_key is what the local store dedupes on, so it's also the
    -- canonical key here. file_hash + file_path are local-only and
    -- intentionally omitted.
    match_key       TEXT PRIMARY KEY,
    random_seed     BIGINT NOT NULL DEFAULT 0,
    map_name        TEXT NOT NULL,
    mode            TEXT NOT NULL,
    build           INTEGER NOT NULL,
    protocol_build  INTEGER NOT NULL,
    played_at       TIMESTAMPTZ NOT NULL,
    duration_s      INTEGER NOT NULL,
    winner_team     INTEGER NOT NULL,
    bans_team0      TEXT NOT NULL DEFAULT '',
    bans_team1      TEXT NOT NULL DEFAULT '',
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_replays_played_at ON replays(played_at);
CREATE INDEX IF NOT EXISTS idx_replays_map       ON replays(map_name);
CREATE INDEX IF NOT EXISTS idx_replays_mode      ON replays(mode);
CREATE INDEX IF NOT EXISTS idx_replays_inserted  ON replays(inserted_at);

CREATE TABLE IF NOT EXISTS players (
    toon_handle     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_players_name ON players(display_name);

CREATE TABLE IF NOT EXISTS player_match (
    match_key                  TEXT NOT NULL,
    slot                       SMALLINT NOT NULL,
    toon_handle                TEXT NOT NULL,
    display_name               TEXT NOT NULL,
    hero                       TEXT NOT NULL,
    hero_id                    TEXT NOT NULL DEFAULT '',
    skin                       TEXT NOT NULL DEFAULT '',
    banner                     TEXT NOT NULL DEFAULT '',
    team                       SMALLINT NOT NULL,
    result                     SMALLINT NOT NULL,
    kills                      INTEGER NOT NULL,
    deaths                     INTEGER NOT NULL,
    assists                    INTEGER NOT NULL,
    takedowns                  INTEGER NOT NULL,
    solo_kills                 INTEGER NOT NULL,
    level                      INTEGER NOT NULL,
    hero_damage                BIGINT NOT NULL,
    siege_damage               BIGINT NOT NULL,
    structure_damage           BIGINT NOT NULL,
    creep_damage               BIGINT NOT NULL DEFAULT 0,
    minion_damage              BIGINT NOT NULL DEFAULT 0,
    minion_kills               INTEGER NOT NULL DEFAULT 0,
    summon_damage              BIGINT NOT NULL DEFAULT 0,
    physical_damage            BIGINT NOT NULL DEFAULT 0,
    spell_damage               BIGINT NOT NULL DEFAULT 0,
    healing                    BIGINT NOT NULL,
    self_healing               BIGINT NOT NULL,
    damage_taken               BIGINT NOT NULL,
    damage_soaked              BIGINT NOT NULL DEFAULT 0,
    teamfight_hero_damage      BIGINT NOT NULL DEFAULT 0,
    teamfight_healing          BIGINT NOT NULL DEFAULT 0,
    teamfight_damage_taken     BIGINT NOT NULL DEFAULT 0,
    teamfight_escapes          INTEGER NOT NULL DEFAULT 0,
    experience_contribution    BIGINT NOT NULL,
    time_spent_dead            INTEGER NOT NULL,
    time_on_point              INTEGER NOT NULL DEFAULT 0,
    merc_camp_captures         INTEGER NOT NULL,
    watch_tower_captures       INTEGER NOT NULL DEFAULT 0,
    regen_globes               INTEGER NOT NULL DEFAULT 0,
    town_kills                 INTEGER NOT NULL DEFAULT 0,
    meta_experience            BIGINT NOT NULL DEFAULT 0,
    time_cc_enemy_heroes       INTEGER NOT NULL DEFAULT 0,
    time_stunning_enemy_heroes INTEGER NOT NULL DEFAULT 0,
    time_rooting_enemy_heroes  INTEGER NOT NULL DEFAULT 0,
    time_silencing_enemy_heroes INTEGER NOT NULL DEFAULT 0,
    highest_kill_streak        INTEGER NOT NULL DEFAULT 0,
    multikill                  INTEGER NOT NULL DEFAULT 0,
    escapes_performed          INTEGER NOT NULL DEFAULT 0,
    vengeances_performed       INTEGER NOT NULL DEFAULT 0,
    outnumbered_deaths         INTEGER NOT NULL DEFAULT 0,
    clutch_heals               INTEGER NOT NULL DEFAULT 0,
    protection_given_to_allies BIGINT NOT NULL DEFAULT 0,
    on_fire_time               INTEGER NOT NULL DEFAULT 0,
    talents                    TEXT NOT NULL DEFAULT '',
    awards                     TEXT NOT NULL DEFAULT '',
    hero_mastery_tiers         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (match_key, slot)
);

CREATE INDEX IF NOT EXISTS idx_pm_toon   ON player_match(toon_handle);
CREATE INDEX IF NOT EXISTS idx_pm_name   ON player_match(display_name);
CREATE INDEX IF NOT EXISTS idx_pm_hero   ON player_match(hero);

-- =============================================================
-- Row-level security
-- =============================================================

-- Squad-only setup: anyone who knows the anon key can read AND write.
-- Switch to a stricter scheme later by:
--   * disabling these "anon" policies,
--   * using ``auth.users`` + email magic-links,
--   * scoping rows by ``squad_id`` columns.

ALTER TABLE replays       ENABLE ROW LEVEL SECURITY;
ALTER TABLE players       ENABLE ROW LEVEL SECURITY;
ALTER TABLE player_match  ENABLE ROW LEVEL SECURITY;

-- The anon role represents requests from the public anon key. We allow
-- it both read and write since the key is shared only inside the squad.
DROP POLICY IF EXISTS "squad anon read replays"      ON replays;
DROP POLICY IF EXISTS "squad anon write replays"     ON replays;
DROP POLICY IF EXISTS "squad anon read players"      ON players;
DROP POLICY IF EXISTS "squad anon write players"     ON players;
DROP POLICY IF EXISTS "squad anon read player_match" ON player_match;
DROP POLICY IF EXISTS "squad anon write player_match" ON player_match;

CREATE POLICY "squad anon read replays"
    ON replays FOR SELECT TO anon USING (true);
CREATE POLICY "squad anon write replays"
    ON replays FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "squad anon update replays"
    ON replays FOR UPDATE TO anon USING (true) WITH CHECK (true);

CREATE POLICY "squad anon read players"
    ON players FOR SELECT TO anon USING (true);
CREATE POLICY "squad anon write players"
    ON players FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "squad anon update players"
    ON players FOR UPDATE TO anon USING (true) WITH CHECK (true);

CREATE POLICY "squad anon read player_match"
    ON player_match FOR SELECT TO anon USING (true);
CREATE POLICY "squad anon write player_match"
    ON player_match FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "squad anon update player_match"
    ON player_match FOR UPDATE TO anon USING (true) WITH CHECK (true);
