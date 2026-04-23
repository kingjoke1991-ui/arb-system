from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/marketdata", tags=["marketdata"])


@router.get("/books")
async def books(c: Container = Depends(get_container)) -> dict:
    out = []
    for snap in c.book_mgr.latest_snapshots():
        out.append(
            {
                "exchange": snap.exchange,
                "symbol": snap.symbol,
                "best_bid": str(snap.best_bid) if snap.best_bid else None,
                "best_ask": str(snap.best_ask) if snap.best_ask else None,
                "mid_price": str(snap.mid_price) if snap.mid_price else None,
                "top_bid_size": str(snap.top_bid_size()),
                "top_ask_size": str(snap.top_ask_size()),
                "ts_local": snap.ts_local.isoformat(),
                "ts_exchange": snap.ts_exchange.isoformat() if snap.ts_exchange else None,
                "latency_ms": snap.latency_ms,
                "stale": c.book_mgr.is_stale(snap.exchange, snap.symbol),
            }
        )
    return {"books": out}
