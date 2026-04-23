"""
Report service: produces daily-ish aggregates from the DB. Simple MVP.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.storage.db import Database
from app.storage.orm_models import HedgeGroup, Opportunity, OrderRow


class ReportService:
    def __init__(self, db: Database):
        self._db = db

    async def summary(self, hours: int = 24) -> dict:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self._db.session() as s:
            opp_count = (
                await s.execute(
                    select(func.count()).select_from(Opportunity).where(Opportunity.detected_at >= since)
                )
            ).scalar_one()
            accepted_count = (
                await s.execute(
                    select(func.count())
                    .select_from(Opportunity)
                    .where(Opportunity.detected_at >= since, Opportunity.decision == "accepted")
                )
            ).scalar_one()
            hedges_completed = (
                await s.execute(
                    select(func.count())
                    .select_from(HedgeGroup)
                    .where(HedgeGroup.created_at >= since, HedgeGroup.state == "completed")
                )
            ).scalar_one()
            hedges_aborted = (
                await s.execute(
                    select(func.count())
                    .select_from(HedgeGroup)
                    .where(HedgeGroup.created_at >= since, HedgeGroup.state == "aborted")
                )
            ).scalar_one()
            realized_pnl = (
                await s.execute(
                    select(func.coalesce(func.sum(HedgeGroup.realized_profit_quote), 0)).where(
                        HedgeGroup.created_at >= since
                    )
                )
            ).scalar_one() or Decimal(0)
            orders_count = (
                await s.execute(
                    select(func.count()).select_from(OrderRow).where(OrderRow.created_at >= since)
                )
            ).scalar_one()

        return {
            "window_hours": hours,
            "opportunities": int(opp_count or 0),
            "opportunities_accepted": int(accepted_count or 0),
            "hedges_completed": int(hedges_completed or 0),
            "hedges_aborted": int(hedges_aborted or 0),
            "orders": int(orders_count or 0),
            "realized_pnl_quote": str(realized_pnl),
        }
