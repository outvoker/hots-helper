import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { compact, kda, pct } from "../../lib/format";
import {
  Empty,
  ErrorState,
  Loading,
  PageHead,
  Pagination,
  WinrateBar,
  useAsync,
} from "../../components/common";
import { MatchRow } from "../matches/MatchRow";

const PAGE = 25;

export default function PlayerDetailPage() {
  const { handle = "" } = useParams();
  const [offset, setOffset] = useState(0);

  const profile = useAsync(() => api.player(handle), [handle]);
  const matches = useAsync(
    () => api.playerMatches(handle, PAGE, offset),
    [handle, offset],
  );

  if (profile.loading) return <Loading />;
  if (profile.error) return <ErrorState message={profile.error} />;
  const p = profile.data!;

  return (
    <>
      <PageHead
        title={p.display_name || handle}
        subtitle={`${p.total_games} 场 · 胜率 ${pct(p.winrate)} · KDA ${kda(
          p.overall_kda.k,
          p.overall_kda.d,
          p.overall_kda.a,
        )}`}
      />
      <p>
        <Link to="/players">← 返回战力榜</Link>
      </p>

      <div className="grid cols-4">
        <div className="stat-tile">
          <div className="label">近 30 天</div>
          <div className="value">{pct(p.recent_winrate)}</div>
          <div className="muted">{p.recent_games} 场</div>
        </div>
        <div className="stat-tile">
          <div className="label">场均英雄伤害</div>
          <div className="value">{compact(p.avg_hero_dmg)}</div>
        </div>
        <div className="stat-tile">
          <div className="label">场均治疗</div>
          <div className="value">{compact(p.avg_healing)}</div>
        </div>
        <div className="stat-tile">
          <div className="label">常用英雄</div>
          <div className="value" style={{ fontSize: "1.1rem" }}>
            {p.signature_heroes[0]?.hero ?? "—"}
          </div>
        </div>
      </div>

      {p.signature_heroes.length > 0 && (
        <section className="card" style={{ marginTop: "1.5rem" }}>
          <h2 className="section-title">英雄池</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>英雄</th>
                  <th>场次</th>
                  <th>胜率</th>
                  <th>K/D/A</th>
                  <th>英雄伤害</th>
                </tr>
              </thead>
              <tbody>
                {p.signature_heroes.slice(0, 12).map((h) => (
                  <tr key={h.hero}>
                    <td>{h.hero}</td>
                    <td>{h.games}</td>
                    <td>
                      <WinrateBar value={h.winrate} /> {pct(h.winrate)}
                    </td>
                    <td>{kda(h.avg_k, h.avg_d, h.avg_a)}</td>
                    <td>{compact(h.avg_hero_dmg)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card" style={{ marginTop: "1.5rem" }}>
        <h2 className="section-title">对局记录</h2>
        {matches.loading ? (
          <Loading />
        ) : !matches.data || matches.data.matches.length === 0 ? (
          <Empty />
        ) : (
          <>
            <div className="match-list">
              {matches.data.matches.map((m) => (
                <MatchRow key={m.replay_id} match={m} highlightHandle={handle} />
              ))}
            </div>
            <Pagination
              total={matches.data.total}
              limit={PAGE}
              offset={offset}
              onPage={setOffset}
            />
          </>
        )}
      </section>

      {p.frequent_teammates.length > 0 && (
        <p className="muted" style={{ marginTop: "1rem" }}>
          常见队友：
          {p.frequent_teammates
            .map((t) => `${t.display_name}(${t.games}场)`)
            .join("、")}
        </p>
      )}
    </>
  );
}
