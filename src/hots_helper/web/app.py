"""FastAPI application factory for the HotS Helper web service.

Serves the JSON API under ``/api`` and, in production, the built React
SPA from ``web/static`` (everything else falls through to the SPA's
``index.html`` so client-side deep links work).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .data import StoreProvider, provider_from_env
from .routers import bp, heroes, matches, meta, players, rankings, weekly

logging.basicConfig(level=logging.INFO)

_STATIC_DIR = Path(__file__).with_name("static")


def create_app(provider: StoreProvider | None = None) -> FastAPI:
    """Build the FastAPI app.

    ``provider`` is injectable for tests (point it at a seeded Store);
    in production it's built from environment variables.
    """
    prov = provider or provider_from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        prov.startup()
        await prov.start_refresh_loop()
        try:
            yield
        finally:
            prov.shutdown()

    app = FastAPI(title="HotS Helper", lifespan=lifespan)
    app.state.provider = prov

    for module in (meta, heroes, players, rankings, bp, matches, weekly):
        app.include_router(module.router)

    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Serve the built SPA if present.

    Mounted last so it never shadows ``/api``. A catch-all returns
    ``index.html`` for unknown paths so React Router routes resolve on
    hard refresh / deep link.
    """
    if not _STATIC_DIR.is_dir():
        return

    app.mount(
        "/assets",
        StaticFiles(directory=_STATIC_DIR / "assets"),
        name="assets",
    )
    index = _STATIC_DIR / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = _STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
