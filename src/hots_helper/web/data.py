"""Store provisioning for the web service.

The web backend is **read-only**: it never ingests replays itself. Its
data comes from the squad's Supabase project, pulled into a local
SQLite via the existing :class:`hots_helper.sync.CloudSync`. All the
analysis modules then run unchanged against that SQLite.

:class:`StoreProvider` owns the lifecycle:

* pick a DB (bundled snapshot env var, or a fresh temp DB synced from
  Supabase, or — if neither is configured — an empty DB so the app
  still boots and the SPA renders "no data" states);
* run a periodic background refresh that re-pulls from Supabase;
* cache the expensive :class:`PowerBaseline` so the player-rankings
  endpoint doesn't rebuild it on every request.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from ..db import Store
from ..player_rank import PowerBaseline, build_power_baseline

logger = logging.getLogger("hots_helper.web")

_DEFAULT_REFRESH_SECONDS = 600


class StoreProvider:
    """Owns the long-lived :class:`Store` and its refresh loop.

    Thread-safety: the underlying SQLite connection is opened with
    ``check_same_thread=False`` and is shared across FastAPI's threadpool
    workers. The only writer is :meth:`refresh` (via ``CloudSync``),
    which guards its inserts with the store's ``RLock``. After each
    refresh we drop the read snapshot so subsequent reads see new rows.
    """

    def __init__(
        self,
        *,
        supabase_url: str = "",
        supabase_key: str = "",
        db_path: str | None = None,
        refresh_seconds: int = _DEFAULT_REFRESH_SECONDS,
    ) -> None:
        self._supabase_url = supabase_url
        self._supabase_key = supabase_key
        self._explicit_db_path = db_path
        self._refresh_seconds = refresh_seconds

        self._store: Store | None = None
        self._tempdir: tempfile.TemporaryDirectory | None = None
        self._baseline: PowerBaseline | None = None
        self._refresh_task: asyncio.Task | None = None

    # --- lifecycle ----------------------------------------------------------

    @property
    def syncs_from_cloud(self) -> bool:
        return bool(self._supabase_url and self._supabase_key)

    def startup(self) -> None:
        """Open the store and perform the first (blocking) cloud pull.

        Safe to call from the FastAPI lifespan; the initial sync runs
        inline so the first requests already see data. Network failures
        are logged, not fatal — the app boots with whatever it has.
        """
        if self._explicit_db_path:
            db_path = Path(self._explicit_db_path)
            logger.info("Opening bundled DB at %s", db_path)
            self._store = Store(db_path)
        else:
            self._tempdir = tempfile.TemporaryDirectory(prefix="hots-web-")
            db_path = Path(self._tempdir.name) / "hots.db"
            self._store = Store(db_path)
            if self.syncs_from_cloud:
                self._sync_blocking()
            else:
                logger.warning(
                    "No SUPABASE_URL / SUPABASE_ANON_KEY configured and no "
                    "HOTS_DB_PATH — serving an empty database."
                )
        self._rebuild_baseline()

    async def start_refresh_loop(self) -> None:
        """Kick off the periodic refresh task (no-op without cloud sync)."""
        if not self.syncs_from_cloud or self._explicit_db_path:
            return
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    def shutdown(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None
        if self._store is not None:
            self._store.close()
            self._store = None
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    # --- accessors ----------------------------------------------------------

    def store(self) -> Store:
        if self._store is None:
            raise RuntimeError("StoreProvider.startup() was not called")
        return self._store

    def baseline(self) -> PowerBaseline:
        if self._baseline is None:
            self._rebuild_baseline()
        assert self._baseline is not None
        return self._baseline

    # --- internals ----------------------------------------------------------

    def _rebuild_baseline(self) -> None:
        if self._store is None:
            return
        try:
            self._baseline = build_power_baseline(self._store)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to build power baseline")
            self._baseline = None

    def _sync_blocking(self) -> None:
        """One synchronous cloud pull. Swallows errors (logs them)."""
        from ..sync import make_sync

        try:
            sync = make_sync(self._store, self._supabase_url, self._supabase_key)
            if sync is None or not sync.is_enabled():
                logger.warning("Cloud sync not enabled; skipping pull")
                return
            result = sync.sync_now()
            logger.info(
                "Cloud sync pulled %d replays / %d players / %d matches",
                result.pulled_replays,
                result.pulled_players,
                result.pulled_player_matches,
            )
            if result.errors:
                logger.warning("Sync reported errors: %s", result.errors)
        except Exception:
            logger.exception("Cloud sync failed")

    async def _refresh_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                await asyncio.sleep(self._refresh_seconds)
                await loop.run_in_executor(None, self._sync_blocking)
                # New rows are invisible to readers holding the old read
                # snapshot until we commit; drop it so the next SELECT
                # sees the freshly pulled data.
                self._store.drop_read_snapshot()  # type: ignore[union-attr]
                await loop.run_in_executor(None, self._rebuild_baseline)
                logger.info("Background refresh complete")
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                logger.exception("Background refresh iteration failed")


def provider_from_env() -> StoreProvider:
    """Build a :class:`StoreProvider` from environment variables.

    * ``HOTS_DB_PATH`` — open this SQLite directly, skip cloud sync.
    * ``SUPABASE_URL`` / ``SUPABASE_ANON_KEY`` — pull from Supabase.
      When unset, fall back to the squad's built-in defaults in
      :mod:`hots_helper.sync_defaults` (the ``sb_publishable_…`` anon key
      is safe to ship in source), so a deployment works out of the box
      and only needs ``HOTS_ACCESS_PASSWORD`` configured. Set the env
      vars to point a private deployment at a different project.
    * ``HOTS_REFRESH_SECONDS`` — refresh interval (default 600).
    """
    from ..sync_defaults import DEFAULT_SUPABASE_ANON_KEY, DEFAULT_SUPABASE_URL

    refresh = os.environ.get("HOTS_REFRESH_SECONDS")
    try:
        refresh_seconds = int(refresh) if refresh else _DEFAULT_REFRESH_SECONDS
    except ValueError:
        refresh_seconds = _DEFAULT_REFRESH_SECONDS
    return StoreProvider(
        supabase_url=os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL,
        supabase_key=os.environ.get("SUPABASE_ANON_KEY") or DEFAULT_SUPABASE_ANON_KEY,
        db_path=os.environ.get("HOTS_DB_PATH") or None,
        refresh_seconds=refresh_seconds,
    )
