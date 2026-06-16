import type {
  BanCandidate,
  HeroAggregate,
  HeroReport,
  MatchDetail,
  MatchListRow,
  OpponentProfile,
  Paginated,
  PickCandidate,
  PlayerRankRow,
  PlayerSummary,
  Reference,
  SquadCandidates,
  Stats,
  WeeklyReport,
} from "./types";

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export interface MatchFilters {
  map?: string;
  mode?: string;
  player?: string;
  result?: number;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export const api = {
  stats: () => get<Stats>("/api/stats"),
  reference: () => get<Reference>("/api/reference"),

  heroes: (map?: string) => get<HeroAggregate[]>("/api/heroes", { map }),
  hero: (hero: string, map?: string) =>
    get<HeroReport>(`/api/heroes/${encodeURIComponent(hero)}`, { map }),

  searchPlayers: (name: string, map?: string) =>
    get<PlayerSummary[]>("/api/players", { name, map }),
  player: (handle: string, map?: string) =>
    get<PlayerSummary>(`/api/players/${encodeURIComponent(handle)}`, { map }),
  playerMatches: (handle: string, limit = 25, offset = 0) =>
    get<Paginated<MatchListRow>>(
      `/api/players/${encodeURIComponent(handle)}/matches`,
      { limit, offset },
    ),

  rankings: (minGames = 5, hero?: string, squad?: string, mode?: string) =>
    get<PlayerRankRow[]>("/api/rankings/players", {
      min_games: minGames,
      hero,
      squad,
      mode,
    }),

  squadCandidates: (minGames = 10, limit = 60) =>
    get<SquadCandidates>("/api/squad/candidates", {
      min_games: minGames,
      limit,
    }),

  matches: (f: MatchFilters) =>
    get<Paginated<MatchListRow>>("/api/matches", {
      map: f.map,
      mode: f.mode,
      player: f.player,
      result: f.result,
      from: f.from,
      to: f.to,
      limit: f.limit,
      offset: f.offset,
    }),
  match: (id: number) => get<MatchDetail>(`/api/matches/${id}`),

  bpProfile: (names: string[], map?: string) =>
    post<OpponentProfile[]>("/api/bp/profile", { names, map }),
  bpBans: (names: string[], alreadyBanned: string[] = []) =>
    post<BanCandidate[]>("/api/bp/bans", { names, already_banned: alreadyBanned }),
  bpPicks: (map: string, excludeHeroes: string[] = []) =>
    post<PickCandidate[]>("/api/bp/picks", { map, exclude_heroes: excludeHeroes }),

  weekly: (days = 7, squad?: string) =>
    get<WeeklyReport>("/api/weekly", { days, squad }),
};
