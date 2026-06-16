import { useMemo, useState } from "react";
import { api } from "../../lib/api";
import type { SquadCandidate } from "../../lib/types";
import { ErrorState, Loading, useAsync } from "../../components/common";
import "./squad.css";

interface SquadPickerProps {
  /** Currently-selected handles to pre-check (e.g. when re-editing). */
  initial?: string[];
  /** Called with the chosen handles when the user confirms. */
  onSave: (handles: string[]) => void;
  /** Optional cancel affordance (shown only when provided). */
  onCancel?: () => void;
  /** Heading copy — differs between first-run and re-edit. */
  title?: string;
  subtitle?: string;
}

/**
 * Roster chooser. Lists frequent players (most games first) as toggle
 * chips; the user picks who counts as "their squad". Used both to gate
 * first entry into the weekly report and to re-edit the roster later.
 */
export default function SquadPicker({
  initial,
  onSave,
  onCancel,
  title = "选择你的小队成员",
  subtitle = "勾选与你常组队的玩家。周报和战力榜高亮都会按这份名单计算，之后可随时重选。",
}: SquadPickerProps) {
  const data = useAsync(() => api.squadCandidates(), []);
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(initial ?? []),
  );
  const [query, setQuery] = useState("");
  // First-run users haven't picked anything; offer the heuristic guess.
  const [seededSuggestion, setSeededSuggestion] = useState(false);

  const candidates = data.data?.candidates ?? [];
  const suggested = data.data?.suggested ?? [];

  // One-time: if re-editing with no prior selection, seed from suggestion.
  if (
    !seededSuggestion &&
    !data.loading &&
    (initial === undefined || initial.length === 0) &&
    suggested.length > 0 &&
    selected.size === 0
  ) {
    setSelected(new Set(suggested));
    setSeededSuggestion(true);
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return candidates;
    return candidates.filter(
      (c) =>
        c.display_name.toLowerCase().includes(q) ||
        c.toon_handle.toLowerCase().includes(q),
    );
  }, [candidates, query]);

  if (data.loading) return <Loading />;
  if (data.error) return <ErrorState message={data.error} />;

  const toggle = (handle: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(handle)) next.delete(handle);
      else next.add(handle);
      return next;
    });
  };

  const selectedList = candidates.filter((c) => selected.has(c.toon_handle));

  return (
    <div className="squad-picker">
      <header className="squad-picker__head">
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </header>

      <div className="squad-picker__bar">
        <input
          type="search"
          placeholder="搜索玩家名 / handle…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="搜索玩家"
        />
        <span className="squad-picker__count">已选 {selected.size} 人</span>
      </div>

      {selectedList.length > 0 && (
        <div className="squad-picker__selected" aria-label="已选成员">
          {selectedList.map((c) => (
            <button
              key={c.toon_handle}
              className="chip chip--on"
              onClick={() => toggle(c.toon_handle)}
              title="点击移除"
            >
              {c.display_name} <span aria-hidden>✕</span>
            </button>
          ))}
        </div>
      )}

      <ul className="squad-picker__list" role="listbox" aria-multiselectable>
        {filtered.map((c: SquadCandidate) => {
          const on = selected.has(c.toon_handle);
          return (
            <li key={c.toon_handle}>
              <button
                role="option"
                aria-selected={on}
                className={`squad-row${on ? " squad-row--on" : ""}`}
                onClick={() => toggle(c.toon_handle)}
              >
                <span className="squad-row__check" aria-hidden>
                  {on ? "✓" : ""}
                </span>
                <span className="squad-row__name">{c.display_name}</span>
                <span className="squad-row__handle mono">{c.toon_handle}</span>
                <span className="squad-row__games">{c.games} 场</span>
              </button>
            </li>
          );
        })}
        {filtered.length === 0 && (
          <li className="squad-picker__empty">没有匹配的玩家</li>
        )}
      </ul>

      <footer className="squad-picker__actions">
        {onCancel && (
          <button className="btn secondary" onClick={onCancel}>
            取消
          </button>
        )}
        <button
          className="btn"
          disabled={selected.size === 0}
          onClick={() => onSave(Array.from(selected))}
        >
          保存名单（{selected.size}）
        </button>
      </footer>
    </div>
  );
}
