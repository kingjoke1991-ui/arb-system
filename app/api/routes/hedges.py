from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_container, require_admin
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/hedges", tags=["hedges"])


@router.get("/active")
async def active(c: Container = Depends(get_container)) -> dict:
    return {
        "hedges": [
            {
                "id": h.hedge_group_id,
                "opportunity_id": h.opportunity_id,
                "symbol": h.symbol,
                "buy_exchange": h.buy_exchange,
                "sell_exchange": h.sell_exchange,
                "target_amount": str(h.target_amount),
                "state": h.state.value,
                "executed_buy_amount": str(h.executed_buy_amount),
                "executed_sell_amount": str(h.executed_sell_amount),
                "net_position_base": str(h.net_position_base),
                "realized_pnl_quote": str(h.realized_pnl_quote) if h.realized_pnl_quote is not None else None,
                "repair_attempts": h.repair_attempts,
                "created_at": h.created_at.isoformat(),
                "updated_at": h.updated_at.isoformat(),
            }
            for h in c.hedge.active_hedges()
        ]
    }


@router.get("/recent")
async def recent(c: Container = Depends(get_container)) -> dict:
    if c.hedge_repo is not None:
        rows = await c.hedge_repo.recent()
        return {
            "hedges": [
                {
                    "id": r.id,
                    "symbol": r.symbol,
                    "state": r.state,
                    "buy_exchange": r.buy_exchange,
                    "sell_exchange": r.sell_exchange,
                    "executed_buy_amount": str(r.executed_buy_amount),
                    "executed_sell_amount": str(r.executed_sell_amount),
                    "net_position_base": str(r.net_position_base),
                    "realized_profit_quote": (
                        str(r.realized_profit_quote) if r.realized_profit_quote is not None else None
                    ),
                    "repair_attempts": r.repair_attempts,
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in rows
            ]
        }
    return {
        "hedges": [
            {
                "id": h.hedge_group_id,
                "symbol": h.symbol,
                "state": h.state.value,
                "buy_exchange": h.buy_exchange,
                "sell_exchange": h.sell_exchange,
                "executed_buy_amount": str(h.executed_buy_amount),
                "executed_sell_amount": str(h.executed_sell_amount),
                "net_position_base": str(h.net_position_base),
                "realized_pnl_quote": str(h.realized_pnl_quote) if h.realized_pnl_quote is not None else None,
                "repair_attempts": h.repair_attempts,
                "created_at": h.created_at.isoformat(),
                "updated_at": h.updated_at.isoformat(),
            }
            for h in c.hedge.all_hedges()
        ]
    }


@router.post("/{hedge_id}/repair", dependencies=[Depends(require_admin)])
async def repair(hedge_id: str, c: Container = Depends(get_container)) -> dict:
    g = c.hedge.get(hedge_id)
    if not g:
        raise HTTPException(status_code=404, detail="not_found")
    updated = await c.repair.repair(g)
    if c.hedge_repo:
        try:
            await c.hedge_repo.upsert(updated)
        except Exception:  # noqa: BLE001
            pass
    return {"id": updated.hedge_group_id, "state": updated.state.value}
