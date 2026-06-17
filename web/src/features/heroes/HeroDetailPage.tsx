import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { pct } from "../../lib/format";
import {
  ErrorState,
  Loading,
  PageHead,
  WinrateBar,
  useAsync,
} from "../../components/common";

export default function HeroDetailPage() {
  const { hero = "" } = useParams();
  const report = useAsync(() => api.hero(hero), [hero]);

  if (report.loading) return <Loading />;
  if (report.error) return <ErrorState message={report.error} />;
  const r = report.data!;

  const tiers = Object.entries(r.talents_by_tier).sort(
    (a, b) => Number(a[0]) - Number(b[0]),
  );

  return (
    <>
      <PageHead
        title={r.hero}
        subtitle={`${r.total_games} 场 · 胜率 ${pct(r.winrate)}`}
      />
      <p>
        <Link to="/heroes">← 返回英雄列表</Link>
      </p>

      <section className="card">
        <h2 className="section-title">地图胜率</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>地图</th>
                <th>场次</th>
                <th>胜场</th>
                <th>胜率</th>
              </tr>
            </thead>
            <tbody>
              {r.map_records
                .slice()
                .sort((a, b) => b.games - a.games)
                .map((m) => (
                  <tr key={m.map_name}>
                    <td>{m.map_name}</td>
                    <td>{m.games}</td>
                    <td>{m.wins}</td>
                    <td>
                      <WinrateBar value={m.winrate} /> {pct(m.winrate)}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </section>

      {tiers.length > 0 && (
        <section className="card">
          <h2 className="section-title">天赋选择（各层按选取率）</h2>
          <p className="muted" style={{ marginTop: "-0.5rem" }}>
            想看按胜率的加点推荐？前往 <Link to="/talents">天赋推荐</Link>。
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>层级</th>
                  <th>天赋</th>
                  <th>场次</th>
                  <th>胜率</th>
                  <th>选取率</th>
                </tr>
              </thead>
              <tbody>
                {tiers.flatMap(([tier, choices]) =>
                  choices
                    .slice()
                    .sort((a, b) => b.pick_rate - a.pick_rate)
                    .map((c, i) => (
                      <tr key={`${tier}-${c.talent}`}>
                        <td>{i === 0 ? `T${tier}` : ""}</td>
                        <td>{c.talent_label || c.talent}</td>
                        <td>{c.games}</td>
                        <td>{c.games ? pct(c.wins / c.games) : "—"}</td>
                        <td>{pct(c.pick_rate)}</td>
                      </tr>
                    )),
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
