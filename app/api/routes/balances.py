from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.runtime.dependency_container import Container

router = APIRouter(tags=["accounts"])


@router.get("/balances")
async def balances(c: Container = Depends(get_container)) -> dict:
    return {
        "balances": [
            {
                "exchange": b.exchange,
                "asset": b.asset,
                "free": str(b.free),
                "locked": str(b.locked),
                "total": str(b.total),
                "ts_local": b.ts_local.isoformat(),
            }
            for b in c.balance_mgr.all()
        ]
    }


@router.get("/inventory")
async def inventory(c: Container = Depends(get_container)) -> dict:
    # Aggregate per asset across exchanges.
    agg: dict[str, dict] = {}
    for b in c.balance_mgr.all():
        d = agg.setdefault(b.asset, {"asset": b.asset, "total": 0, "by_exchange": {}})
        d["by_exchange"][b.exchange] = str(b.total)
        d["total"] = float(d["total"]) + float(b.total)
    return {"inventory": [{**v, "total": str(v["total"])} for v in agg.values()]}
