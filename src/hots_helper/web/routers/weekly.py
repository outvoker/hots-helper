"""Weekly squad report."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...db import Store
from ...weekly_report import build_weekly_report, format_weekly_brief
from .. import serialize
from ..deps import get_store

router = APIRouter(prefix="/api/weekly", tags=["weekly"])


@router.get("")
def weekly(
    days: int = Query(7, ge=1, le=3650),
    store: Store = Depends(get_store),
) -> dict:
    report = build_weekly_report(store, days=days)
    brief = format_weekly_brief(report)
    return serialize.weekly_report(report, brief=brief)
