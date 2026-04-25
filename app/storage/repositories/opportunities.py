from __future__ import annotations

from sqlalchemy import desc, select

from app.models.opportunity import ArbitrageOpportunity
from app.storage.db import Database
from app.storage.orm_models import Opportunity


class OpportunityRepo:
    def __init__(self, db: Database):
        self._db = db

    async def save(
        self, opp: ArbitrageOpportunity, decision: str | None = None, reason: str | None = None
    ) -> None:
        async with self._db.session() as s:
            row = Opportunity(
                id=opp.opportunity_id,
                symbol=opp.symbol,
                buy_exchange=opp.buy_exchange,
                sell_exchange=opp.sell_exchange,
                buy_price=opp.buy_price,
                sell_price=opp.sell_price,
                gross_spread_bps=opp.gross_spread_bps,
                buy_fee_bps=opp.buy_fee_bps,
                sell_fee_bps=opp.sell_fee_bps,
                slippage_bps=opp.slippage_bps,
                buffer_bps=opp.buffer_bps,
                net_edge_bps=opp.net_edge_bps,
                max_tradable_size=opp.max_tradable_size,
                expected_profit_quote=opp.expected_profit_quote,
                detected_at=opp.detected_at,
                decision=decision,
                decision_reason=reason,
                buy_book_age_ms=opp.buy_book_age_ms,
                sell_book_age_ms=opp.sell_book_age_ms,
            )
            s.add(row)
            await s.commit()

    async def recent(self, limit: int = 50) -> list[Opportunity]:
        async with self._db.session() as s:
            q = select(Opportunity).order_by(desc(Opportunity.detected_at)).limit(limit)
            res = await s.execute(q)
            return list(res.scalars().all())
