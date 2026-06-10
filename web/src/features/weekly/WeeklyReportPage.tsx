import { useState } from "react";
import { api } from "../../lib/api";
import { compact, pct, shortDate } from "../../lib/format";
import {
  Empty,
  ErrorState,
  Loading,
  PageHead,
  WinrateBar,
  useAsync,
} from "../../components/common";

export default function WeeklyReportPage() {
  const [days, setDays] = useState(7);
  const report = useAsync(() => api.weekly(days), [days]);

  if (report.loading) return <Loading />;
  if (report.error) return <ErrorState message={report.error} />;
  const r = report.data!;
  const cur = r.overview.current;

  return (
    <>
      <PageHead title="战队周报" subtitle="可分享的滚动战绩复盘" />

      <div className="filters">
        <label className="muted">
          时间窗口：
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>7 天</option>
            <option value={14}>14 天</option>
            <option value={30}>30 天</option>
          </select>
        </label>
      </div>

      {cur.games === 0 ? (
        <Empty message={`最近 ${days} 天没有对局。`} />
      ) : (
        <>
          <div className="grid cols-4">
            <div className="stat-tile">
              <div className="label">场次</div>
              <div className="value">{cur.games}</div>
            </div>
            <div className="stat-tile">
              <div className="label">胜率</div>
              <div className="value">{pct(cur.winrate)}</div>
            </div>
            <div className="stat-tile">
              <div className="label">较上周胜率</div>
              <div className="value">
                {r.overview.winrate_delta_pp >= 0 ? "+" : ""}
                {r.overview.winrate_delta_pp.toFixed(0)}pp
              </div>
            </div>
            <div className="stat-tile">
              <div className="label">最长连胜</div>
              <div className="value">{r.longest_win_streak.length}</div>
            </div>
          </div>

          {r.awards.length > 0 && (
            <section className="card" style={{ marginTop: "1.5rem" }}>
              <h2 className="section-title">🏆 本期奖项</h2>
              <div className="grid cols-3">
                {r.awards.map((a) => (
                  <div key={a.label_key} className="stat-tile">
                    <div className="label">{a.label}</div>
                    <div className="value" style={{ fontSize: "1.1rem" }}>
                      {a.display_name}
                    </div>
                    <div className="muted">
                      {a.hero} · {a.games} 场
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="card" style={{ marginTop: "1.5rem" }}>
            <h2 className="section-title">队员战绩</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>玩家</th>
                    <th>场次</th>
                    <th>胜率</th>
                    <th>常用英雄</th>
                  </tr>
                </thead>
                <tbody>
                  {r.players.map((p) => (
                    <tr key={p.toon_handle}>
                      <td>{p.display_name}</td>
                      <td>{p.games}</td>
                      <td>
                        <WinrateBar value={p.winrate} /> {pct(p.winrate)}
                      </td>
                      <td>
                        {p.most_played_hero}（{p.most_played_hero_wins}/
                        {p.most_played_hero_games}）
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {r.highlights.length > 0 && (
            <section className="card" style={{ marginTop: "1.5rem" }}>
              <h2 className="section-title">高光时刻</h2>
              <ul>
                {r.highlights.map((h, i) => (
                  <li key={i}>
                    {shortDate(h.played_at)} · {h.display_name} 的 {h.hero}（
                    {h.map_name}）{h.kills}/{h.deaths}/{h.assists} ·{" "}
                    {compact(h.hero_damage)} 伤害
                  </li>
                ))}
              </ul>
            </section>
          )}

          {r.brief && (
            <section className="card" style={{ marginTop: "1.5rem" }}>
              <h2 className="section-title">复制文本</h2>
              <textarea
                readOnly
                value={r.brief}
                style={{
                  width: "100%",
                  minHeight: "200px",
                  background: "var(--color-void)",
                  color: "var(--color-text)",
                  border: "1px solid var(--color-border-strong)",
                  borderRadius: "var(--radius-sm)",
                  padding: "var(--space-3)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-xs)",
                }}
              />
            </section>
          )}
        </>
      )}
    </>
  );
}
