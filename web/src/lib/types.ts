// Response types mirroring src/hots_helper/web/serialize.py.

export interface Stats {
  replays: number;
  players: number;
  by_mode: Record<string, number>;
}

export interface Reference {
  storm_league_maps: string[];
  aram_maps: string[];
  heroes: string[];
  hero_roles: Record<string, string>;
}

export interface HeroAggregate {
  hero: string;
  games: number;
  wins: number;
  winrate: number;
  wilson_lb: number;
  avg_k: number;
  avg_d: number;
  avg_a: number;
  avg_hero_dmg: number;
  avg_siege_dmg: number;
  avg_healing: number;
}

export interface MapRecord {
  map_name: string;
  games: number;
  wins: number;
  winrate: number;
}

export interface TalentChoice {
  talent: string;
  talent_label: string;
  games: number;
  wins: number;
  pick_rate: number;
}

export interface HeroReport {
  hero: string;
  total_games: number;
  total_wins: number;
  winrate: number;
  map_games: number;
  map_wins: number;
  map_winrate: number;
  map_records: MapRecord[];
  talents_by_tier: Record<string, TalentChoice[]>;
}

export interface HeroUsage {
  hero: string;
  hero_id: string;
  games: number;
  wins: number;
  winrate: number;
  avg_k: number;
  avg_d: number;
  avg_a: number;
  avg_hero_dmg: number;
  avg_healing: number;
  last_played: string;
}

export interface RecentMatch {
  played_at: string;
  map_name: string;
  mode: string;
  hero: string;
  result: number;
  kills: number;
  deaths: number;
  assists: number;
  hero_damage: number;
  healing: number;
}

export interface TeammateEntry {
  display_name: string;
  toon_handle: string;
  games: number;
  shared_wins: number;
  shared_winrate: number;
}

export interface PlayerSummary {
  name_searched: string;
  toon_handle: string;
  display_name: string;
  total_games: number;
  total_wins: number;
  winrate: number;
  overall_kda: { k: number; d: number; a: number };
  recent_games: number;
  recent_wins: number;
  recent_winrate: number;
  map_games: number;
  map_wins: number;
  map_winrate: number;
  avg_hero_dmg: number;
  avg_healing: number;
  signature_heroes: HeroUsage[];
  map_heroes: HeroUsage[];
  frequent_teammates: TeammateEntry[];
  frequent_opponents: TeammateEntry[];
  recent_matches: RecentMatch[];
  map_records: MapRecord[];
  ban_recommendations: HeroUsage[];
  note: string;
}

export interface PlayerRankRow {
  rank: number;
  toon_handle: string;
  display_name: string;
  games: number;
  wins: number;
  win_rate: number;
  wilson_lb: number;
  kda: number;
  avg_hero_dmg: number;
  avg_healing: number;
  avg_dmg_taken: number;
  power: number;
  last_seen_at: string;
  is_squad: boolean;
}

export interface SquadCandidate {
  toon_handle: string;
  display_name: string;
  games: number;
}

export interface TalentChoice {
  talent: string;
  talent_label: string;
  games: number;
  wins: number;
  pick_rate: number;
  win_rate: number;
  wilson_lb: number;
}

export interface TalentTier {
  tier: number;
  recommended: TalentChoice | null;
  choices: TalentChoice[];
}

export interface TalentBuild {
  hero: string;
  mode_group: string;
  total_games: number;
  total_wins: number;
  win_rate: number;
  tiers: TalentTier[];
}

export interface SquadCandidates {
  candidates: SquadCandidate[];
  suggested: string[];
}

export interface MatchListRow {
  replay_id: number;
  match_key: string;
  map_name: string;
  mode: string;
  played_at: string;
  duration_s: number;
  winner_team: number;
  bans_team0: string[];
  bans_team1: string[];
  team0: { hero: string; display_name: string }[];
  team1: { hero: string; display_name: string }[];
}

export interface Paginated<T> {
  total: number;
  limit: number;
  offset: number;
  matches: T[];
}

export interface MatchPlayer {
  slot: number;
  team: number;
  toon_handle: string;
  display_name: string;
  hero: string;
  result: number;
  kills: number;
  deaths: number;
  assists: number;
  hero_damage: number;
  siege_damage: number;
  structure_damage: number;
  healing: number;
  damage_taken: number;
  experience_contribution: number;
  level: number;
}

export interface MatchDetail {
  replay_id: number;
  map_name: string;
  mode: string;
  played_at: string;
  duration_s: number;
  winner_team: number;
  bans_team0: string[];
  bans_team1: string[];
  players: MatchPlayer[];
}

export interface ThreatHero {
  hero: string;
  games: number;
  wins: number;
  raw_winrate: number;
  wilson_lb: number;
  lift_pp: number;
  p_value: number;
}

export interface OpponentProfile {
  name_searched: string;
  display_name: string;
  total_games: number;
  threats: ThreatHero[];
  note: string;
  power: number;
  power_rank: number;
  power_total: number;
  ally_games: number;
  ally_wins: number;
  enemy_games: number;
  enemy_wins: number;
}

export interface BanCandidate {
  hero: string;
  score: number;
  total_games: number;
  total_wins: number;
  combined_wr: number;
  contributors: { name: string; games: number; wins: number; wilson_lb: number }[];
}

export interface TalentPick {
  tier: number;
  talent: string;
  pick_rate: number;
  wilson_lb: number;
}

export interface PickCandidate {
  hero: string;
  map_games: number;
  map_wins: number;
  map_winrate: number;
  map_wilson_lb: number;
  global_winrate: number;
  lift_pp: number;
  p_value: number;
  significant: boolean;
  recommended_build: TalentPick[];
}

export interface WeeklyWindow {
  days: number;
  start_iso: string;
  end_iso: string;
  games: number;
  wins: number;
  winrate: number;
}

export interface WeeklyAward {
  label_key: string;
  label: string;
  display_name: string;
  hero: string;
  value: number;
  games: number;
}

export interface WeeklyReport {
  overview: {
    current: WeeklyWindow;
    previous: WeeklyWindow;
    games_delta: number;
    winrate_delta_pp: number;
  };
  players: {
    toon_handle: string;
    display_name: string;
    games: number;
    wins: number;
    winrate: number;
    avg_k: number;
    avg_d: number;
    avg_a: number;
    most_played_hero: string;
    most_played_hero_games: number;
    most_played_hero_wins: number;
  }[];
  awards: WeeklyAward[];
  highlights: {
    played_at: string;
    display_name: string;
    hero: string;
    map_name: string;
    result: number;
    kills: number;
    deaths: number;
    assists: number;
    hero_damage: number;
  }[];
  hero_top_picked: { hero: string; games: number; wins: number; winrate: number }[];
  hero_top_winrate: { hero: string; games: number; wins: number; winrate: number }[];
  hero_combos: {
    hero_a: string;
    hero_b: string;
    games: number;
    wins: number;
    winrate: number;
  }[];
  maps: { map_name: string; games: number; wins: number; winrate: number }[];
  longest_win_streak: { length: number; started_at: string; ended_at: string };
  longest_loss_streak: { length: number; started_at: string; ended_at: string };
  brief: string;
}
