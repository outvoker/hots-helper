"""FastAPI dependencies.

Endpoints are declared as plain ``def`` (not ``async def``) so FastAPI
runs them in its threadpool — keeping the blocking SQLite reads and the
CPU-bound analysis off the event loop. These helpers pull the shared
:class:`StoreProvider` off ``app.state``.
"""

from __future__ import annotations

from fastapi import Request

from ..db import Store
from ..player_rank import PowerBaseline
from .data import StoreProvider


def get_provider(request: Request) -> StoreProvider:
    return request.app.state.provider


def get_store(request: Request) -> Store:
    return request.app.state.provider.store()


def get_baseline(request: Request) -> PowerBaseline:
    return request.app.state.provider.baseline()
