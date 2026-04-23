from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/dependencies")
async def dependencies(c: Container = Depends(get_container)) -> dict:
    db_ok = False
    if c.db:
        db_ok = await c.db.ping()
    return {
        "db": db_ok,
        "adapters": [a.name for a in c.registry.all()],
    }


@router.get("/exchanges")
async def exchanges(c: Container = Depends(get_container)) -> dict:
    results = c.health.check_all(c.registry.names())
    return {
        "exchanges": [
            {
                "name": h.exchange,
                "marketdata_ok": h.marketdata_ok,
                "balance_ok": h.balance_ok,
                "reason": h.reason,
            }
            for h in results
        ],
        "kill_switch": c.kill.status(),
        "circuit_breaker": c.breaker.status(),
    }
