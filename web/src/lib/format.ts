export function pct(x: number): string {
  return `${Math.round(x * 100)}%`;
}

export function pct1(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}

export function kda(k: number, d: number, a: number): string {
  return `${k.toFixed(1)}/${d.toFixed(1)}/${a.toFixed(1)}`;
}

export function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

export function date(iso: string): string {
  if (!iso) return "";
  // 2026-05-01T20:00:00+00:00 -> 2026-05-01 20:00
  return iso.slice(0, 16).replace("T", " ");
}

export function shortDate(iso: string): string {
  return iso ? iso.slice(5, 10) : "";
}

// DB result convention: 1 = win, 2 = loss.
export function isWin(result: number): boolean {
  return result === 1;
}
