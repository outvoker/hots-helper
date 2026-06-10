import { useState } from "react";
import { api, type MatchFilters } from "../../lib/api";
import {
  Empty,
  ErrorState,
  Loading,
  PageHead,
  Pagination,
  useAsync,
} from "../../components/common";
import { MatchRow } from "./MatchRow";

const PAGE = 25;

export default function MatchListPage() {
  const [map, setMap] = useState("");
  const [mode, setMode] = useState("Storm League");
  const [player, setPlayer] = useState("");
  const [playerInput, setPlayerInput] = useState("");
  const [offset, setOffset] = useState(0);

  const ref = useAsync(() => api.reference(), []);

  const filters: MatchFilters = {
    map: map || undefined,
    mode: mode === "全部" ? "" : mode,
    player: player || undefined,
    limit: PAGE,
    offset,
  };
  const matches = useAsync(() => api.matches(filters), [
    map,
    mode,
    player,
    offset,
  ]);

  function applyPlayer() {
    setOffset(0);
    setPlayer(playerInput.trim());
  }

  return (
    <>
      <PageHead title="比赛记录" subtitle="战队全部对局，按时间倒序" />

      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          applyPlayer();
        }}
      >
        <select
          value={mode}
          onChange={(e) => {
            setOffset(0);
            setMode(e.target.value);
          }}
        >
          <option value="Storm League">Storm League</option>
          <option value="ARAM">ARAM</option>
          <option value="全部">全部模式</option>
        </select>
        <select
          value={map}
          onChange={(e) => {
            setOffset(0);
            setMap(e.target.value);
          }}
        >
          <option value="">全部地图</option>
          {ref.data?.storm_league_maps.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <input
          type="search"
          placeholder="按玩家名筛选…"
          value={playerInput}
          onChange={(e) => setPlayerInput(e.target.value)}
        />
        <button className="btn" type="submit">
          筛选
        </button>
        {player && (
          <button
            type="button"
            className="btn secondary"
            onClick={() => {
              setPlayer("");
              setPlayerInput("");
              setOffset(0);
            }}
          >
            清除「{player}」
          </button>
        )}
      </form>

      {matches.loading ? (
        <Loading />
      ) : matches.error ? (
        <ErrorState message={matches.error} />
      ) : !matches.data || matches.data.matches.length === 0 ? (
        <Empty />
      ) : (
        <>
          <div className="match-list">
            {matches.data.matches.map((m) => (
              <MatchRow
                key={m.replay_id}
                match={m}
                highlightHandle={player || undefined}
              />
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
    </>
  );
}
