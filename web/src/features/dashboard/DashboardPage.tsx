import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { pct, shortDate } from "../../lib/format";
import { ErrorState, Loading, PageHead, useAsync } from "../../components/common";

export default function DashboardPage() {
  const stats = useAsync(() => api.stats(), []);
  const weekly = useAsync(() => api.weekly(7), []);

  if (stats.loading) return <Loading />;
  if (stats.error) return <ErrorState message={stats.error} />;
  const s = stats.data!;
  const w = weekly.data;

  return (
    <>
      <PageHead title="战队总览" subtitle="本地复盘数据汇总，实时从云端同步" />

      <div className="grid cols-3">
        <div className="stat-tile">
          <div className="label">已收录对局</div>
          <div className="value">{s.replays}</div>
        </div>
        <div className="stat-tile">
          <div className="label">出现过的玩家</div>
          <div className="value">{s.players}</div>
        </div>
        <div className="stat-tile">
          <div className="label">Storm League 场次</div>
          <div className="value">{s.by_mode["Storm League"] ?? 0}</div>
        </div>
      </div>

      {w && (
        <section className="card" style={{ marginTop: "1.5rem" }}>
          <h2 className="section-title">近 7 天战绩</h2>
          {w.overview.current.games === 0 ? (
            <p className="muted">最近 7 天没有对局记录。</p>
          ) : (
            <>
              <div className="grid cols-3">
                <div className="stat-tile">
                  <div className="label">场次</div>
                  <div className="value">{w.overview.current.games}</div>
                </div>
                <div className="stat-tile">
                  <div className="label">胜率</div>
                  <div className="value">{pct(w.overview.current.winrate)}</div>
                </div>
                <div className="stat-tile">
                  <div className="label">较上周</div>
                  <div className="value">
                    {w.overview.winrate_delta_pp >= 0 ? "+" : ""}
                    {w.overview.winrate_delta_pp.toFixed(0)}pp
                  </div>
                </div>
              </div>
              <p style={{ marginTop: "1rem" }}>
                <Link to="/weekly">查看完整周报 →</Link>
              </p>
            </>
          )}
        </section>
      )}

      <section className="card" style={{ marginTop: "1.5rem" }}>
        <h2 className="section-title">各模式分布</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>模式</th>
                <th>场次</th>
                <th>占比</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(s.by_mode).map(([mode, n]) => (
                <tr key={mode}>
                  <td>{mode}</td>
                  <td>{n}</td>
                  <td>{s.replays ? pct(n / s.replays) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {w && w.highlights.length > 0 && (
        <p className="muted" style={{ marginTop: "1rem" }}>
          最近高光：{w.highlights[0].display_name} 的 {w.highlights[0].hero}（
          {shortDate(w.highlights[0].played_at)}）
        </p>
      )}
    </>
  );
}
