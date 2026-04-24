"""Persistence for the runtime ConfigService snapshot.

We keep a single row keyed by ``"current"`` whose ``payload`` is a JSON map
``{settings_attr: stringified_value}``. The ConfigService reads this on boot
to override the pydantic Settings defaults, and writes it on every
``update()`` so operator edits survive container restarts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.common.clock import utcnow
from app.storage.db import Database
from app.storage.orm_models import ConfigSnapshotRow

_CURRENT_KEY = "current"


class ConfigSnapshotRepo:
    def __init__(self, db: Database):
        self._db = db

    async def load(self) -> dict[str, Any] | None:
        """Return the latest persisted snapshot payload, or ``None`` if none."""
        async with self._db.session() as s:
            res = await s.execute(select(ConfigSnapshotRow).where(ConfigSnapshotRow.key == _CURRENT_KEY))
            row = res.scalar_one_or_none()
            if row is None:
                return None
            return dict(row.payload or {})

    async def save(self, payload: dict[str, Any], actor: str = "api") -> datetime:
        """Upsert the canonical row. Existing keys are *replaced* atomically.

        Returns the new ``updated_at`` so callers can log it.
        """
        ts = utcnow()
        async with self._db.session() as s:
            res = await s.execute(select(ConfigSnapshotRow).where(ConfigSnapshotRow.key == _CURRENT_KEY))
            row = res.scalar_one_or_none()
            if row is None:
                row = ConfigSnapshotRow(
                    key=_CURRENT_KEY,
                    payload=payload,
                    updated_at=ts,
                    actor=actor,
                )
                s.add(row)
            else:
                row.payload = payload
                row.updated_at = ts
                row.actor = actor
            await s.commit()
            return ts
