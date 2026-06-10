import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { compact, kda, pct } from "../../lib/format";
import {
  Empty,
  ErrorState,
  Loading,
  PageHead,
  WinrateBar,
  useAsync,
} from "../../components/common";
import type { HeroAggregate } from "../../lib/types";

type SortKey = "wilson_lb" | "winrate" | "games" | "hero";

export default function HeroRankingsPage() {
  const [map, setMap] = useState("");
  const [minGames, setMinGames] = useState(3);
  const [sort, setSort] = useState<SortKey>("wilson_lb");

  const ref = useAsync(() => api.reference(), []);
  const heroes = useAsync(() => api.heroes(map || undefined), [map]);

  if (heroes.loading) return <Loading />;
  if (heroes.error) return <ErrorState message={heroes.error} />;

  const rows = (heroes.data ?? [])
    .filter((h) => h.games >= minGames)
    .sort((a, b) => sortHeroes(a, b, sort));

  return (
    <>
      <PageHead title="英雄强度" subtitle="按 Wilson 95% 置信下界排序，小样本不会霸榜" />

      <div className="filters">
        <select value={map} onChange={(e) => setMap(e.target.value)}>
          <option value="">全部地图</option>
          {ref.data?.storm_league_maps.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <label className="muted">
          最少场次：
          <select
            value={minGames}
            onChange={(e) => setMinGames(Number(e.target.value))}
          >
            {[1, 3, 5, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      {rows.length === 0 ? (
        <Empty />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <Th label="英雄" k="hero" sort={sort} setSort={setSort} />
                <Th label="场次" k="games" sort={sort} setSort={setSort} />
                <th>胜场</th>
                <Th label="胜率" k="winrate" sort={sort} setSort={setSort} />
                <Th label="置信下界" k="wilson_lb" sort={sort} setSort={setSort} />
                <th>K/D/A</th>
                <th>英雄伤害</th>
                <th>治疗</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((h) => (
                <tr key={h.hero}>
                  <td>
                    <Link to={`/heroes/${encodeURIComponent(h.hero)}`}>
                      {h.hero}
                    </Link>
                  </td>
                  <td>{h.games}</td>
                  <td>{h.wins}</td>
                  <td>
                    <WinrateBar value={h.winrate} /> {pct(h.winrate)}
                  </td>
                  <td>{pct(h.wilson_lb)}</td>
                  <td>{kda(h.avg_k, h.avg_d, h.avg_a)}</td>
                  <td>{compact(h.avg_hero_dmg)}</td>
                  <td>{compact(h.avg_healing)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function sortHeroes(a: HeroAggregate, b: HeroAggregate, k: SortKey): number {
  if (k === "hero") return a.hero.localeCompare(b.hero);
  return b[k] - a[k];
}

function Th({
  label,
  k,
  sort,
  setSort,
}: {
  label: string;
  k: SortKey;
  sort: SortKey;
  setSort: (k: SortKey) => void;
}) {
  return (
    <th className="sortable" onClick={() => setSort(k)}>
      {label}
      {sort === k ? " ↓" : ""}
    </th>
  );
}
