import { useState } from "react";
import { api } from "../../lib/api";
import { pct } from "../../lib/format";
import { ErrorState, Loading, PageHead, useAsync } from "../../components/common";
import type { BanCandidate, OpponentProfile, PickCandidate } from "../../lib/types";

export default function BpAdvisorPage() {
  const ref = useAsync(() => api.reference(), []);
  const [map, setMap] = useState("");
  const [names, setNames] = useState<string[]>(["", "", "", "", ""]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<OpponentProfile[] | null>(null);
  const [bans, setBans] = useState<BanCandidate[] | null>(null);
  const [picks, setPicks] = useState<PickCandidate[] | null>(null);

  async function run() {
    const enemyNames = names.map((n) => n.trim()).filter(Boolean);
    setLoading(true);
    setError(null);
    try {
      const [pr, bn, pk] = await Promise.all([
        enemyNames.length ? api.bpProfile(enemyNames, map || undefined) : Promise.resolve([]),
        enemyNames.length ? api.bpBans(enemyNames) : Promise.resolve([]),
        map ? api.bpPicks(map) : Promise.resolve([]),
      ]);
      setProfiles(pr);
      setBans(bn);
      setPicks(pk);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageHead title="BP 助手" subtitle="输入对手名字 + 当前地图，给出禁用与拿手英雄建议" />

      <section className="card">
        <div className="filters">
          <select value={map} onChange={(e) => setMap(e.target.value)}>
            <option value="">选择地图…</option>
            {ref.data?.storm_league_maps.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div className="grid cols-3">
          {names.map((n, i) => (
            <input
              key={i}
              type="text"
              placeholder={`对手 ${i + 1}`}
              value={n}
              onChange={(e) => {
                const next = names.slice();
                next[i] = e.target.value;
                setNames(next);
              }}
            />
          ))}
        </div>
        <div style={{ marginTop: "1rem" }}>
          <button className="btn" onClick={run} disabled={loading}>
            {loading ? "分析中…" : "开始分析"}
          </button>
        </div>
      </section>

      {error && <ErrorState message={error} />}
      {loading && <Loading />}

      {bans && bans.length > 0 && (
        <section className="card">
          <h2 className="section-title">🚫 推荐禁用</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>英雄</th>
                  <th>威胁分</th>
                  <th>合计战绩</th>
                  <th>来自</th>
                </tr>
              </thead>
              <tbody>
                {bans.map((b) => (
                  <tr key={b.hero}>
                    <td>{b.hero}</td>
                    <td>{b.score.toFixed(2)}</td>
                    <td>
                      {b.total_wins}/{b.total_games}（{pct(b.combined_wr)}）
                    </td>
                    <td style={{ textAlign: "left" }}>
                      {b.contributors
                        .map((c) => `${c.name} ${c.wins}/${c.games}`)
                        .join("、")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {picks && picks.length > 0 && (
        <section className="card">
          <h2 className="section-title">✅ 推荐拿手（{map}）</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>英雄</th>
                  <th>地图场次</th>
                  <th>地图胜率</th>
                  <th>置信下界</th>
                  <th>对比全图</th>
                  <th>推荐天赋</th>
                </tr>
              </thead>
              <tbody>
                {picks.map((p) => (
                  <tr key={p.hero}>
                    <td>
                      {p.significant ? "✓ " : ""}
                      {p.hero}
                    </td>
                    <td>{p.map_games}</td>
                    <td>{pct(p.map_winrate)}</td>
                    <td>{pct(p.map_wilson_lb)}</td>
                    <td>
                      {p.lift_pp >= 0 ? "+" : ""}
                      {p.lift_pp.toFixed(0)}pp
                    </td>
                    <td style={{ textAlign: "left" }}>
                      {p.recommended_build
                        .map((t) => `T${t.tier}:${t.talent}`)
                        .join("  ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {profiles && profiles.length > 0 && (
        <section className="card">
          <h2 className="section-title">对手分析</h2>
          {profiles.map((prof) => (
            <article key={prof.name_searched} style={{ marginBottom: "1rem" }}>
              <h3>
                {prof.display_name}{" "}
                <span className="muted">
                  {prof.total_games} 场
                  {prof.power > 0 && ` · 战力 ${prof.power.toFixed(0)}`}
                </span>
              </h3>
              {prof.note && <p className="muted">{prof.note}</p>}
              {prof.threats.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
                  {prof.threats.map((t) => (
                    <span key={t.hero} className="chip">
                      {t.hero} {pct(t.raw_winrate)}（{t.wins}/{t.games}）
                    </span>
                  ))}
                </div>
              )}
            </article>
          ))}
        </section>
      )}
    </>
  );
}
