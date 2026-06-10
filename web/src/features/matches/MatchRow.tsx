import { Link } from "react-router-dom";
import { date } from "../../lib/format";
import type { MatchListRow } from "../../lib/types";
import "./matches.css";

/** A single match summary card: two team columns of heroes, the winner
    side tinted. When ``highlightHandle`` matches a player, their row is
    emphasised so the per-player history reads at a glance. */
export function MatchRow({
  match,
  highlightHandle,
}: {
  match: MatchListRow;
  highlightHandle?: string;
}) {
  const winner = match.winner_team;
  return (
    <Link to={`/matches/${match.replay_id}`} className="match-row">
      <div className="match-meta">
        <span className="map">{match.map_name}</span>
        <span className="muted mode">{match.mode}</span>
        <time className="muted">{date(match.played_at)}</time>
      </div>
      <div className="teams">
        <TeamSide
          players={match.team0}
          won={winner === 0}
          highlight={highlightHandle}
        />
        <span className="vs">VS</span>
        <TeamSide
          players={match.team1}
          won={winner === 1}
          highlight={highlightHandle}
          mirror
        />
      </div>
    </Link>
  );
}

function TeamSide({
  players,
  won,
  highlight,
  mirror,
}: {
  players: { hero: string; display_name: string }[];
  won: boolean;
  highlight?: string;
  mirror?: boolean;
}) {
  return (
    <div className={`team ${won ? "won" : "lost"} ${mirror ? "mirror" : ""}`}>
      <span className={`outcome ${won ? "tag-win" : "tag-loss"}`}>
        {won ? "胜" : "负"}
      </span>
      <ul className="heroes">
        {players.map((p, i) => (
          <li
            key={i}
            className={highlight && p.display_name === highlight ? "me" : ""}
            title={p.display_name}
          >
            {p.hero}
          </li>
        ))}
      </ul>
    </div>
  );
}
