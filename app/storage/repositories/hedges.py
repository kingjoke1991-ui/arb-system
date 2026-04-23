from __future__ import annotations

from sqlalchemy import desc, select

from app.common.clock import utcnow
from app.models.hedge import HedgeGroupState
from app.storage.db import Database
from app.storage.orm_models import HedgeGroup


class HedgeRepo:
    def __init__(self, db: Database):
        self._db = db

    async def upsert(self, g: HedgeGroupState) -> None:
        async with self._db.session() as s:
            existing = await s.get(HedgeGroup, g.hedge_group_id)
            if existing is None:
                existing = HedgeGroup(
                    id=g.hedge_group_id,
                    opportunity_id=g.opportunity_id,
                    symbol=g.symbol,
                    buy_exchange=g.buy_exchange,
                    sell_exchange=g.sell_exchange,
                    target_amount=g.target_amount,
                    state=g.state.value,
                    executed_buy_amount=g.executed_buy_amount,
                    executed_sell_amount=g.executed_sell_amount,
                    net_position_base=g.net_position_base,
                    expected_profit_quote=g.expected_profit_quote,
                    realized_profit_quote=g.realized_pnl_quote,
                    repair_attempts=g.repair_attempts,
                    notes=" | ".join(g.notes) if g.notes else None,
                    created_at=g.created_at,
                    updated_at=utcnow(),
                )
                s.add(existing)
            else:
                existing.state = g.state.value
                existing.executed_buy_amount = g.executed_buy_amount
                existing.executed_sell_amount = g.executed_sell_amount
                existing.net_position_base = g.net_position_base
                existing.realized_profit_quote = g.realized_pnl_quote
                existing.repair_attempts = g.repair_attempts
                existing.notes = " | ".join(g.notes) if g.notes else None
                existing.updated_at = utcnow()
            await s.commit()

    async def recent(self, limit: int = 50) -> list[HedgeGroup]:
        async with self._db.session() as s:
            q = select(HedgeGroup).order_by(desc(HedgeGroup.created_at)).limit(limit)
            res = await s.execute(q)
            return list(res.scalars().all())

    async def active(self) -> list[HedgeGroup]:
        async with self._db.session() as s:
            q = select(HedgeGroup).where(~HedgeGroup.state.in_(["completed", "aborted"]))
            res = await s.execute(q)
            return list(res.scalars().all())
