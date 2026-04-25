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


@router.get("/trade-quality")
async def trade_quality(
    limit: int = Query(100, ge=1, le=500),
    c: Container = Depends(get_container),
) -> dict:
    """Per-hedge expected-vs-realized report (issue 6)."""
    if c.trade_quality_repo is None:
        return {"error": "db_unavailable"}
    rows = await c.trade_quality_repo.recent(limit=limit)

    def _s(v):
        return None if v is None else str(v)

    return {
        "rows": [
            {
                "hedge_group_id": r.hedge_group_id,
                "opportunity_id": r.opportunity_id,
                "symbol": r.symbol,
                "buy_exchange": r.buy_exchange,
                "sell_exchange": r.sell_exchange,
                "final_state": r.final_state,
                "failure_reason": r.failure_reason,
                "expected_edge_bps": _s(r.expected_edge_bps),
                "expected_profit_quote": _s(r.expected_profit_quote),
                "expected_profit_quote_at_approved_size": _s(r.expected_profit_quote_at_approved_size),
                "expected_edge_bps_at_approved_size": _s(r.expected_edge_bps_at_approved_size),
                "buy_expected_vwap": _s(r.buy_expected_vwap),
                "sell_expected_vwap": _s(r.sell_expected_vwap),
                "buy_actual_vwap": _s(r.buy_actual_vwap),
                "sell_actual_vwap": _s(r.sell_actual_vwap),
                "executed_buy_amount": _s(r.executed_buy_amount),
                "executed_sell_amount": _s(r.executed_sell_amount),
                "actual_fee_quote": _s(r.actual_fee_quote),
                "actual_slippage_quote": _s(r.actual_slippage_quote),
                "repair_cost_quote": _s(r.repair_cost_quote),
                "repair_attempts": r.repair_attempts,
                "net_realized_pnl_quote": _s(r.net_realized_pnl_quote),
                "latency_ms_signal_to_order": r.latency_ms_signal_to_order,
                "latency_ms_order_to_fill": r.latency_ms_order_to_fill,
                "buy_book_age_ms": r.buy_book_age_ms,
                "sell_book_age_ms": r.sell_book_age_ms,
                "mode": r.mode,
                "finished_at": r.finished_at.isoformat(),
            }
            for r in rows
        ]
    }
