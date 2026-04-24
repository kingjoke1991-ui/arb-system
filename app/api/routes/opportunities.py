from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container
from app.execution.funding_executor import recent_hedges as recent_funding_hedges
from app.execution.maker_taker_executor import recent_reports as recent_maker_reports
from app.execution.triangular_executor import recent_executions as recent_tri_execs
from app.runtime.dependency_container import Container
from app.strategy.funding_rate_scanner import opp_to_dict as funding_opp_to_dict
from app.strategy.triangular_scanner import opp_to_dict

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("/recent")
async def recent(limit: int = Query(50, ge=1, le=500), c: Container = Depends(get_container)) -> dict:
    last_scan_at = c.scanner.last_scan_at() if c.scanner else None
    status = {
        "scan_count": c.scanner.scan_count() if c.scanner else 0,
        "last_scan_at": last_scan_at.isoformat() if last_scan_at else None,
        "enabled": c.settings.strategy_cross_exchange_spot_enabled,
        "running": c.scanner.is_running() if c.scanner else False,
    }
    if c.opp_repo is None:
        return {"opportunities": [], "source": "memory_only", **status}
    rows = await c.opp_repo.recent(limit=limit)
    return {
        **status,
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
        ],
    }


@router.get("/triangular/recent")
async def triangular_recent(
    limit: int = Query(50, ge=1, le=200),
    c: Container = Depends(get_container),
) -> dict:
    """Last N triangular opportunities detected in-memory.

    Triangular opps are NOT persisted to the DB (different shape from the
    cross-exchange ``Opportunity`` ORM table). This endpoint reads the
    scanner's ring buffer directly.
    """
    items = c.triangular.recent(limit=limit)
    last_scan_at = c.triangular.last_scan_at()
    return {
        "running": c.triangular.is_running(),
        "enabled": c.settings.strategy_triangular_same_exchange_enabled,
        "session_count": c.triangular.session_count(),
        "scan_count": c.triangular.scan_count(),
        "last_scan_at": last_scan_at.isoformat() if last_scan_at else None,
        "opportunities": [opp_to_dict(o) for o in items],
    }


@router.get("/triangular/executions")
async def triangular_executions(
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """Last N triangular paper-trade executions (3-leg with rollback)."""
    return {"executions": recent_tri_execs(limit=limit)}


@router.get("/funding/executions")
async def funding_executions(
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """Last N funding-rate hedge executions (paper + live)."""
    return {"executions": recent_funding_hedges(limit=limit)}


@router.get("/funding/recent")
async def funding_recent(
    limit: int = Query(50, ge=1, le=200),
    c: Container = Depends(get_container),
) -> dict:
    """Last N funding-rate opportunities (perpetual resonance)."""
    items = c.funding.recent(limit=limit)
    status = c.funding.status()
    return {**status, "opportunities": [funding_opp_to_dict(o) for o in items]}


@router.get("/maker_taker/executions")
async def maker_taker_executions(limit: int = Query(50, ge=1, le=100)) -> dict:
    """Last N maker-taker execution reports (POSTED → DONE / CANCELED / FLAT)."""
    return {"executions": [r.to_dict() for r in recent_maker_reports(limit=limit)]}
