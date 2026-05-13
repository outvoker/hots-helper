CREATE TABLE IF NOT EXISTS replays (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash       TEXT NOT NULL UNIQUE,
    match_key       TEXT NOT NULL,        -- same value for every perspective of this match
    random_seed     INTEGER NOT NULL DEFAULT 0,
    file_path       TEXT NOT NULL,
    map_name        TEXT NOT NULL,
    mode            TEXT NOT NULL,
    build           INTEGER NOT NULL,
    protocol_build  INTEGER NOT NULL,
    played_at       TEXT NOT NULL,
    duration_s      INTEGER NOT NULL,
    winner_team     INTEGER NOT NULL,
    bans_team0      TEXT NOT NULL DEFAULT '',
    bans_team1      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_replays_played_at ON replays(played_at);
CREATE INDEX IF NOT EXISTS idx_replays_map       ON replays(map_name);
CREATE INDEX IF NOT EXISTS idx_replays_mode      ON replays(mode);
CREATE UNIQUE INDEX IF NOT EXISTS uq_replays_match_key ON replays(match_key);

CREATE TABLE IF NOT EXISTS players (
    toon_handle     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_players_name ON players(display_name);

CREATE TABLE IF NOT EXISTS player_match (
    replay_id                  INTEGER NOT NULL,
    slot                       INTEGER NOT NULL,
    toon_handle                TEXT NOT NULL,
    display_name               TEXT NOT NULL,
    hero                       TEXT NOT NULL,        -- localized display name
    hero_id                    TEXT NOT NULL DEFAULT '',
    skin                       TEXT NOT NULL DEFAULT '',
    banner                     TEXT NOT NULL DEFAULT '',
    team                       INTEGER NOT NULL,
    result                     INTEGER NOT NULL,
    kills                      INTEGER NOT NULL,
    deaths                     INTEGER NOT NULL,
    assists                    INTEGER NOT NULL,
    takedowns                  INTEGER NOT NULL,
    solo_kills                 INTEGER NOT NULL,
    level                      INTEGER NOT NULL,
    hero_damage                INTEGER NOT NULL,
    siege_damage               INTEGER NOT NULL,
    structure_damage           INTEGER NOT NULL,
    creep_damage               INTEGER NOT NULL DEFAULT 0,
    minion_damage              INTEGER NOT NULL DEFAULT 0,
    minion_kills               INTEGER NOT NULL DEFAULT 0,
    summon_damage              INTEGER NOT NULL DEFAULT 0,
    physical_damage            INTEGER NOT NULL DEFAULT 0,
    spell_damage               INTEGER NOT NULL DEFAULT 0,
    healing                    INTEGER NOT NULL,
    self_healing               INTEGER NOT NULL,
    damage_taken               INTEGER NOT NULL,
    damage_soaked              INTEGER NOT NULL DEFAULT 0,
    teamfight_hero_damage      INTEGER NOT NULL DEFAULT 0,
    teamfight_healing          INTEGER NOT NULL DEFAULT 0,
    teamfight_damage_taken     INTEGER NOT NULL DEFAULT 0,
    teamfight_escapes          INTEGER NOT NULL DEFAULT 0,
    experience_contribution    INTEGER NOT NULL,
    time_spent_dead            INTEGER NOT NULL,
    time_on_point              INTEGER NOT NULL DEFAULT 0,
    merc_camp_captures         INTEGER NOT NULL,
    watch_tower_captures       INTEGER NOT NULL DEFAULT 0,
    regen_globes               INTEGER NOT NULL DEFAULT 0,
    town_kills                 INTEGER NOT NULL DEFAULT 0,
    meta_experience            INTEGER NOT NULL DEFAULT 0,
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
    protection_given_to_allies INTEGER NOT NULL DEFAULT 0,
    on_fire_time               INTEGER NOT NULL DEFAULT 0,
    talents                    TEXT NOT NULL DEFAULT '',  -- JSON list
    awards                     TEXT NOT NULL DEFAULT '',  -- JSON list
    hero_mastery_tiers         TEXT NOT NULL DEFAULT '',  -- JSON object
    PRIMARY KEY (replay_id, slot),
    FOREIGN KEY (replay_id) REFERENCES replays(id) ON DELETE CASCADE,
    FOREIGN KEY (toon_handle) REFERENCES players(toon_handle)
);

CREATE INDEX IF NOT EXISTS idx_pm_toon   ON player_match(toon_handle);
CREATE INDEX IF NOT EXISTS idx_pm_name   ON player_match(display_name);
CREATE INDEX IF NOT EXISTS idx_pm_hero   ON player_match(hero);
CREATE INDEX IF NOT EXISTS idx_pm_heroid ON player_match(hero_id);
CREATE INDEX IF NOT EXISTS idx_pm_replay ON player_match(replay_id);
