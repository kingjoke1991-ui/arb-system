from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("/recent")
async def recent(limit: int = Query(50, ge=1, le=500), c: Container = Depends(get_container)) -> dict:
    if c.opp_repo is None:
        return {"opportunities": [], "source": "memory_only"}
    rows = await c.opp_repo.recent(limit=limit)
    return {
        "opportunities": [
            {
                "id": r.id,
                "symbol": r.symbol,
                "buy_exchange": r.buy_exchange,
                "sell_exchange": r.sell_exchange,
                "buy_price": str(r.buy_price),
                "sell_price": str(r.sell_price),
                "net_edge_bps": str(r.net_edge_bps),
                "expected_profit_quote": str(r.expected_profit_quote),
                "decision": r.decision,
                "decision_reason": r.decision_reason,
                "detected_at": r.detected_at.isoformat(),
            }
            for r in rows
        ]
    }
