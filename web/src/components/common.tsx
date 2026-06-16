import { useEffect, useState } from "react";
import { pct } from "../lib/format";

/** Tiny async-data hook: tracks loading / error / data for a fetch fn.
 *
 * Pass ``enabled = false`` to defer the fetch (e.g. until a prerequisite
 * like the squad roster is configured); the hook then reports neither
 * loading nor data until it flips true. */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[],
  enabled = true,
): {
  data: T | null;
  loading: boolean;
  error: string | null;
} {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled]);

  return { data, loading, error };
}

export function Loading() {
  return <div className="state">加载中…</div>;
}

export function ErrorState({ message }: { message: string }) {
  return <div className="state error">出错了：{message}</div>;
}

export function Empty({ message = "暂无数据" }: { message?: string }) {
  return <div className="state">{message}</div>;
}

export function WinrateBar({ value }: { value: number }) {
  return (
    <span
      className="winrate-bar"
      style={{ ["--pct" as string]: pct(value) }}
      title={pct(value)}
    />
  );
}

export function PageHead({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="page-head">
      <h1>{title}</h1>
      {subtitle && <p>{subtitle}</p>}
    </header>
  );
}

export function Pagination({
  total,
  limit,
  offset,
  onPage,
}: {
  total: number;
  limit: number;
  offset: number;
  onPage: (offset: number) => void;
}) {
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="toolbar">
      <span className="muted">
        共 {total} 场 · 第 {page} / {pages} 页
      </span>
      <span style={{ display: "flex", gap: "0.5rem" }}>
        <button
          className="btn secondary"
          disabled={offset === 0}
          onClick={() => onPage(Math.max(0, offset - limit))}
        >
          上一页
        </button>
        <button
          className="btn secondary"
          disabled={page >= pages}
          onClick={() => onPage(offset + limit)}
        >
          下一页
        </button>
      </span>
    </div>
  );
}
