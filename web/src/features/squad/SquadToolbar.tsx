import { useState } from "react";
import { useSquad } from "../../lib/squad";
import SquadPicker from "./SquadPicker";

/**
 * Compact "current squad + 重新选择" strip for pages that depend on the
 * roster. Opens the picker in a modal so the user can re-edit at any
 * time without leaving the page. Reads/writes the shared squad store, so
 * saving here updates every page at once.
 */
export default function SquadToolbar() {
  const { squad, save } = useSquad();
  const [editing, setEditing] = useState(false);

  const names =
    squad.handles.length > 0
      ? `${squad.handles.length} 名成员`
      : "未设置";

  return (
    <div className="squad-toolbar">
      <span>
        当前小队：<span className="squad-toolbar__names">{names}</span>
      </span>
      <button className="squad-toolbar__edit" onClick={() => setEditing(true)}>
        重新选择
      </button>

      {editing && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setEditing(false);
          }}
        >
          <div className="modal-body">
            <SquadPicker
              initial={squad.handles}
              title="重新选择小队成员"
              subtitle="更新后，周报与战力榜高亮会立即按新名单刷新。"
              onCancel={() => setEditing(false)}
              onSave={(handles) => {
                save(handles);
                setEditing(false);
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
