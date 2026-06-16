"""FastAPI dependencies.

Endpoints are declared as plain ``def`` (not ``async def``) so FastAPI
runs them in its threadpool — keeping the blocking SQLite reads and the
CPU-bound analysis off the event loop. These helpers pull the shared
:class:`StoreProvider` off ``app.state``.
"""

from __future__ import annotations

from fastapi import Query, Request

from ..db import Store
from ..player_rank import PowerBaseline
from .data import StoreProvider


def get_provider(request: Request) -> StoreProvider:
    return request.app.state.provider


def get_store(request: Request) -> Store:
    return request.app.state.provider.store()


def get_baseline(request: Request) -> PowerBaseline:
    return request.app.state.provider.baseline()


def get_squad(
    squad: str | None = Query(
        None,
        description=(
            "Comma-separated toon_handles of the viewer's configured squad. "
            "When omitted, the server's play-frequency heuristic is used."
        ),
    ),
) -> tuple[str, ...] | None:
    """Parse the optional ``squad`` query param into a handle tuple.

    Returns ``None`` (not an empty tuple) when unset so downstream code
    can distinguish "no roster configured → use heuristic" from "an
    explicit selection". Blank entries are dropped and order-preserving
    de-duplication keeps the request idempotent.
    """
    if squad is None:
        return None
    seen: dict[str, None] = {}
    for raw in squad.split(","):
        handle = raw.strip()
        if handle:
            seen.setdefault(handle, None)
    return tuple(seen) if seen else None
