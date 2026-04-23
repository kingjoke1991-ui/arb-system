"""
Async SQLAlchemy engine/session wrapper. DDL is applied by ``scripts.apply_schema``.
"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.common.logging import get_logger
from app.storage.orm_models import Base

log = get_logger("storage.db")


class Database:
    def __init__(self, dsn: str):
        self._engine: AsyncEngine = create_async_engine(dsn, future=True, pool_pre_ping=True)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def session(self) -> AsyncSession:
        return self._session_factory()

    async def create_all(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("schema_applied")

    async def ping(self) -> bool:
        try:
            async with self._engine.begin() as conn:
                await conn.run_sync(lambda c: c.exec_driver_sql("SELECT 1"))
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("db_ping_failed", error=str(e))
            return False

    async def close(self) -> None:
        await self._engine.dispose()
