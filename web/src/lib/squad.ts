// Squad roster config, persisted per-browser in localStorage.
//
// The web app is a shared, read-only viewer: many people open the same
// deployment behind one password. A server-side "squad" setting would
// let one viewer's roster clobber everyone else's, so the roster lives
// client-side. The selected handles are sent to the API as a `squad`
// query param; the backend falls back to its frequency heuristic when
// the param is absent (first run, before the user has chosen).

import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "hots.squad.v1";

export interface SquadConfig {
  /** Selected toon_handles. Empty array = "configured, but nobody". */
  handles: string[];
  /** Whether the user has completed the picker at least once. */
  configured: boolean;
}

const EMPTY: SquadConfig = { handles: [], configured: false };

function read(): SquadConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<SquadConfig>;
    if (!Array.isArray(parsed.handles)) return EMPTY;
    return {
      handles: parsed.handles.filter((h): h is string => typeof h === "string"),
      configured: parsed.configured === true,
    };
  } catch {
    return EMPTY;
  }
}

// --- a tiny external store so every component re-renders on change ---

const listeners = new Set<() => void>();
let snapshot: SquadConfig = read();

function emit() {
  snapshot = read();
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Reflect roster changes made in another tab.
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) emit();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function saveSquad(handles: string[]): void {
  // De-dupe while preserving pick order.
  const unique = Array.from(new Set(handles.filter(Boolean)));
  const next: SquadConfig = { handles: unique, configured: true };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  emit();
}

export function clearSquad(): void {
  localStorage.removeItem(STORAGE_KEY);
  emit();
}

/** The `squad` query-param value, or undefined when unconfigured/empty. */
export function squadParam(config: SquadConfig): string | undefined {
  if (!config.configured || config.handles.length === 0) return undefined;
  return config.handles.join(",");
}

/** Subscribe to the persisted squad config. */
export function useSquad(): {
  squad: SquadConfig;
  save: (handles: string[]) => void;
  clear: () => void;
} {
  const squad = useSyncExternalStore(subscribe, () => snapshot);
  const save = useCallback((handles: string[]) => saveSquad(handles), []);
  const clear = useCallback(() => clearSquad(), []);
  return { squad, save, clear };
}
