from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/recent")
async def recent_orders(limit: int = Query(50, ge=1, le=500), c: Container = Depends(get_container)) -> dict:
    if c.order_repo is not None:
        rows = await c.order_repo.recent(limit=limit)
        return {
            "orders": [
                {
                    "id": r.id,
                    "hedge_group_id": r.hedge_group_id,
                    "exchange": r.exchange,
                    "exchange_order_id": r.exchange_order_id,
                    "symbol": r.symbol,
                    "side": r.side,
                    "is_repair": r.is_repair,
                    "price": str(r.requested_price) if r.requested_price else None,
                    "amount": str(r.requested_amount),
                    "filled": str(r.filled_amount),
                    "avg_fill_price": str(r.avg_fill_price) if r.avg_fill_price else None,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in rows
            ]
        }
    # memory fallback
    return {
        "orders": [
            {
                "id": o.internal_order_id,
                "hedge_group_id": o.hedge_group_id,
                "exchange": o.exchange,
                "exchange_order_id": o.exchange_order_id,
                "symbol": o.symbol,
                "side": o.side.value,
                "is_repair": o.is_repair,
                "price": str(o.price) if o.price else None,
                "amount": str(o.amount),
                "filled": str(o.filled),
                "avg_fill_price": str(o.avg_fill_price) if o.avg_fill_price else None,
                "status": o.status.value,
                "created_at": o.created_at.isoformat(),
                "updated_at": o.updated_at.isoformat(),
            }
            for o in c.tracker.recent(limit=limit)
        ]
    }
