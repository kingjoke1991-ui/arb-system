from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary")
async def summary(hours: int = Query(24, ge=1, le=168), c: Container = Depends(get_container)) -> dict:
    if c.report is None:
        return {"error": "db_unavailable"}
    return await c.report.summary(hours=hours)
