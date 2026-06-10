import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { compact, date, isWin } from "../../lib/format";
import { ErrorState, Loading, PageHead, useAsync } from "../../components/common";
import type { MatchPlayer } from "../../lib/types";
import "./matches.css";

export default function MatchDetailPage() {
  const { replayId = "" } = useParams();
  const detail = useAsync(() => api.match(Number(replayId)), [replayId]);

  if (detail.loading) return <Loading />;
  if (detail.error) return <ErrorState message={detail.error} />;
  const d = detail.data!;

  const team0 = d.players.filter((p) => p.team === 0);
  const team1 = d.players.filter((p) => p.team === 1);

  return (
    <>
      <PageHead
        title={d.map_name}
        subtitle={`${d.mode} · ${date(d.played_at)} · ${Math.round(
          d.duration_s / 60,
        )} 分钟`}
      />
      <p>
        <Link to="/matches">← 返回比赛记录</Link>
      </p>

      <TeamBlock
        title="队伍 1"
        players={team0}
        won={d.winner_team === 0}
        bans={d.bans_team0}
      />
      <TeamBlock
        title="队伍 2"
        players={team1}
        won={d.winner_team === 1}
        bans={d.bans_team1}
      />
    </>
  );
}

function TeamBlock({
  title,
  players,
  won,
  bans,
}: {
  title: string;
  players: MatchPlayer[];
  won: boolean;
  bans: string[];
}) {
  return (
    <section className="card team-block">
      <h3>
        {title}{" "}
        <span className={won ? "tag-win" : "tag-loss"}>{won ? "胜利" : "失败"}</span>
      </h3>
      {bans.length > 0 && (
        <div className="bans">
          <span className="muted">禁用：</span>
          {bans.map((b) => (
            <span key={b} className="chip ban">
              {b}
            </span>
          ))}
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>玩家</th>
              <th>英雄</th>
              <th>等级</th>
              <th>K/D/A</th>
              <th>英雄伤害</th>
              <th>承伤</th>
              <th>治疗</th>
              <th>经验</th>
            </tr>
          </thead>
          <tbody>
            {players.map((p) => (
              <tr key={p.slot}>
                <td>
                  <Link to={`/players/${encodeURIComponent(p.toon_handle)}`}>
                    {p.display_name}
                  </Link>
                </td>
                <td>{p.hero}</td>
                <td>{p.level}</td>
                <td className={isWin(p.result) ? "" : ""}>
                  {p.kills}/{p.deaths}/{p.assists}
                </td>
                <td>{compact(p.hero_damage)}</td>
                <td>{compact(p.damage_taken)}</td>
                <td>{compact(p.healing)}</td>
                <td>{compact(p.experience_contribution)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
