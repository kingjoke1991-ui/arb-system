"""Trade-quality repository.

Issue 6 — writes one row per finished hedge group capturing expected vs
realized for retrospective analysis. The row is computed from the
``HedgeGroupState`` and (optionally) the originating ``ArbitrageOpportunity``.

Slippage is split out from PnL so the report can distinguish:
  * fees we couldn't avoid (``actual_fee_quote``)
  * price movement between detection and fill (``actual_slippage_quote``)
  * residual-flatten cost (``repair_cost_quote``)
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import desc, select

from app.common.clock import utcnow
from app.models.hedge import HedgeGroupState
from app.models.opportunity import ArbitrageOpportunity
from app.storage.db import Database
from app.storage.orm_models import TradeQuality


def _compute_slippage(g: HedgeGroupState) -> Decimal | None:
    """Return realized − expected slippage in quote ccy.

    A positive value means we paid more / received less than the
    scanner-time VWAP suggested. Negative means we got *better* fills
    (rare, usually means the book moved in our favour mid-flight).
    """
    parts: list[Decimal] = []
    if g.buy_expected_vwap is not None and g.buy_actual_vwap is not None and g.executed_buy_amount > 0:
        parts.append((g.buy_actual_vwap - g.buy_expected_vwap) * g.executed_buy_amount)
    if g.sell_expected_vwap is not None and g.sell_actual_vwap is not None and g.executed_sell_amount > 0:
        parts.append((g.sell_expected_vwap - g.sell_actual_vwap) * g.executed_sell_amount)
    if not parts:
        return None
    return sum(parts, Decimal(0))


class TradeQualityRepo:
    def __init__(self, db: Database):
        self._db = db

    async def write(
        self,
        g: HedgeGroupState,
        opp: ArbitrageOpportunity | None = None,
        mode: str | None = None,
    ) -> None:
        async with self._db.session() as s:
            row = TradeQuality(
                hedge_group_id=g.hedge_group_id,
                opportunity_id=g.opportunity_id,
                symbol=g.symbol,
                buy_exchange=g.buy_exchange,
                sell_exchange=g.sell_exchange,
                final_state=g.state.value,
                failure_reason=g.failure_reason,
                expected_edge_bps=opp.net_edge_bps if opp is not None else None,
                expected_profit_quote=g.expected_profit_quote,
                expected_profit_quote_at_approved_size=g.expected_profit_quote_at_approved_size,
                expected_edge_bps_at_approved_size=g.expected_edge_bps_at_approved_size,
                buy_expected_vwap=g.buy_expected_vwap,
                sell_expected_vwap=g.sell_expected_vwap,
                buy_actual_vwap=g.buy_actual_vwap,
                sell_actual_vwap=g.sell_actual_vwap,
                executed_buy_amount=g.executed_buy_amount,
                executed_sell_amount=g.executed_sell_amount,
                actual_fee_quote=g.actual_fee_quote,
                actual_slippage_quote=_compute_slippage(g),
                repair_cost_quote=g.repair_cost_quote,
                repair_attempts=g.repair_attempts,
                net_realized_pnl_quote=g.realized_pnl_quote,
                latency_ms_signal_to_order=g.latency_ms_signal_to_order,
                latency_ms_order_to_fill=g.latency_ms_order_to_fill,
                buy_book_age_ms=opp.buy_book_age_ms if opp is not None else None,
                sell_book_age_ms=opp.sell_book_age_ms if opp is not None else None,
                mode=mode,
                finished_at=utcnow(),
            )
            s.add(row)
            await s.commit()

    async def recent(self, limit: int = 100) -> list[TradeQuality]:
        async with self._db.session() as s:
            q = select(TradeQuality).order_by(desc(TradeQuality.finished_at)).limit(limit)
            res = await s.execute(q)
            return list(res.scalars().all())
