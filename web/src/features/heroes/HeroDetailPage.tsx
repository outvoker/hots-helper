import { useState } from "react";
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
import TalentBuildSection from "./TalentBuildSection";

export default function HeroDetailPage() {
  const { hero = "" } = useParams();
  const report = useAsync(() => api.hero(hero), [hero]);
  const [mode, setMode] = useState<"standard" | "aram">("standard");
  const build = useAsync(() => api.heroTalents(hero, mode), [hero, mode]);

  if (report.loading) return <Loading />;
  if (report.error) return <ErrorState message={report.error} />;
  const r = report.data!;

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

      <TalentBuildSection
        mode={mode}
        onModeChange={setMode}
        loading={build.loading}
        error={build.error}
        build={build.data}
      />
    </>
  );
}
