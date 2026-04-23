from __future__ import annotations

from sqlalchemy import desc, select

from app.common.clock import utcnow
from app.models.order import UnifiedOrderState
from app.storage.db import Database
from app.storage.orm_models import OrderRow


class OrderRepo:
    def __init__(self, db: Database):
        self._db = db

    async def upsert(self, o: UnifiedOrderState) -> None:
        async with self._db.session() as s:
            existing = await s.get(OrderRow, o.internal_order_id)
            if existing is None:
                existing = OrderRow(
                    id=o.internal_order_id,
                    hedge_group_id=o.hedge_group_id or None,
                    exchange=o.exchange,
                    exchange_order_id=o.exchange_order_id,
                    client_order_id=o.client_order_id,
                    symbol=o.symbol,
                    side=o.side.value,
                    order_type="",  # populate from intent if needed later
                    is_repair=o.is_repair,
                    requested_price=o.price,
                    requested_amount=o.amount,
                    status=o.status.value,
                    filled_amount=o.filled,
                    avg_fill_price=o.avg_fill_price,
                    fee_amount=o.fee_amount,
                    fee_asset=o.fee_asset,
                    raw=o.raw,
                    created_at=o.created_at,
                    updated_at=utcnow(),
                )
                s.add(existing)
            else:
                existing.status = o.status.value
                existing.filled_amount = o.filled
                existing.avg_fill_price = o.avg_fill_price
                existing.fee_amount = o.fee_amount
                existing.fee_asset = o.fee_asset
                existing.updated_at = utcnow()
            await s.commit()

    async def recent(self, limit: int = 50) -> list[OrderRow]:
        async with self._db.session() as s:
            q = select(OrderRow).order_by(desc(OrderRow.created_at)).limit(limit)
            res = await s.execute(q)
            return list(res.scalars().all())
