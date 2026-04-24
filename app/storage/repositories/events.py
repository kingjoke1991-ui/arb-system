from __future__ import annotations

from sqlalchemy import desc, select

from app.storage.db import Database
from app.storage.orm_models import SystemEvent


class EventRepo:
    def __init__(self, db: Database):
        self._db = db

    async def log(
        self,
        event_type: str,
        severity: str,
        component: str,
        message: str,
        payload: dict | None = None,
    ) -> None:
        async with self._db.session() as s:
            row = SystemEvent(
                event_type=event_type,
                severity=severity,
                component=component,
                message=message,
                payload=payload or {},
            )
            s.add(row)
            await s.commit()

    async def recent(self, limit: int = 100) -> list[SystemEvent]:
        async with self._db.session() as s:
            q = select(SystemEvent).order_by(desc(SystemEvent.created_at)).limit(limit)
            res = await s.execute(q)
            return list(res.scalars().all())

    async def recent_by_type(self, event_type: str, limit: int = 100) -> list[SystemEvent]:
        async with self._db.session() as s:
            q = (
                select(SystemEvent)
                .where(SystemEvent.event_type == event_type)
                .order_by(desc(SystemEvent.created_at))
                .limit(limit)
            )
            res = await s.execute(q)
            return list(res.scalars().all())
