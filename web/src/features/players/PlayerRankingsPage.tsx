import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { compact, pct } from "../../lib/format";
import {
  Empty,
  ErrorState,
  Loading,
  PageHead,
  WinrateBar,
  useAsync,
} from "../../components/common";

export default function PlayerRankingsPage() {
  const [minGames, setMinGames] = useState(5);
  const rank = useAsync(() => api.rankings(minGames), [minGames]);

  if (rank.loading) return <Loading />;
  if (rank.error) return <ErrorState message={rank.error} />;
  const rows = rank.data ?? [];

  return (
    <>
      <PageHead
        title="玩家战力榜"
        subtitle="所有与战队同场过的玩家，按综合战力分（全库百分位）排序"
      />

      <div className="filters">
        <label className="muted">
          最少场次：
          <select
            value={minGames}
            onChange={(e) => setMinGames(Number(e.target.value))}
          >
            {[1, 3, 5, 10, 20].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      {rows.length === 0 ? (
        <Empty message="还没有足够的对局来计算战力。" />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>玩家</th>
                <th>战力</th>
                <th>场次</th>
                <th>胜率</th>
                <th>KDA</th>
                <th>英雄伤害</th>
                <th>治疗</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.toon_handle}>
                  <td>{p.rank}</td>
                  <td>
                    <Link to={`/players/${encodeURIComponent(p.toon_handle)}`}>
                      {p.display_name || p.toon_handle}
                    </Link>
                  </td>
                  <td className="mono" style={{ fontWeight: 700 }}>
                    {p.power.toFixed(0)}
                  </td>
                  <td>{p.games}</td>
                  <td>
                    <WinrateBar value={p.win_rate} /> {pct(p.win_rate)}
                  </td>
                  <td>{p.kda.toFixed(2)}</td>
                  <td>{compact(p.avg_hero_dmg)}</td>
                  <td>{compact(p.avg_healing)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
