"""Strategy discovery + toggle API.

GET  /strategies               — list catalog (static) + current enabled flag per id
POST /strategies/{id}/enable   — flip the enable flag (rejected unless status=READY
                                 or DETECT_ONLY). Updates settings in place.
POST /strategies/{id}/disable  — unconditionally flip off.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_container, require_admin
from app.api.rate_limit import rate_limit
from app.runtime.dependency_container import Container
from app.strategy.registry import (
    STRATEGIES,
    StrategyStatus,
)
from app.strategy.registry import (
    get as get_meta,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _enabled_attr(sid: str) -> str:
    return f"strategy_{sid}_enabled"


@router.get("")
async def list_strategies(c: Container = Depends(get_container)) -> dict:
    items = []
    for meta in STRATEGIES:
        flag = getattr(c.settings, _enabled_attr(meta.id), False)
        items.append(
            {
                **asdict(meta),
                "enabled": bool(flag),
                # Readable status badge string for the UI
                "status_zh": {
                    StrategyStatus.READY: "可用",
                    StrategyStatus.DETECT_ONLY: "仅检测（暂不执行）",
                    StrategyStatus.PLANNED: "未实现 / V2",
                }.get(meta.status, meta.status),
            }
        )
    return {"strategies": items}


@router.post("/{sid}/enable", dependencies=[Depends(require_admin), Depends(rate_limit)])
async def enable(sid: str, c: Container = Depends(get_container)) -> dict:
    meta = get_meta(sid)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown strategy: {sid}")
    if meta.status == StrategyStatus.PLANNED:
        raise HTTPException(
            status_code=400,
            detail=f"strategy {sid} is PLANNED and has no implementation yet; enable rejected. See registry description for V2 timeline.",
        )
    attr = _enabled_attr(sid)
    if not hasattr(c.settings, attr):
        raise HTTPException(status_code=500, detail=f"no settings flag for {sid}")
    setattr(c.settings, attr, True)
    return {"id": sid, "enabled": True, "status": meta.status}


@router.post("/{sid}/disable", dependencies=[Depends(require_admin), Depends(rate_limit)])
async def disable(sid: str, c: Container = Depends(get_container)) -> dict:
    meta = get_meta(sid)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown strategy: {sid}")
    attr = _enabled_attr(sid)
    if not hasattr(c.settings, attr):
        raise HTTPException(status_code=500, detail=f"no settings flag for {sid}")
    setattr(c.settings, attr, False)
    return {"id": sid, "enabled": False, "status": meta.status}
