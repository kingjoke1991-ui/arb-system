"""
Async SQLAlchemy engine/session wrapper. DDL is applied by ``scripts.apply_schema``.
"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateColumn

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
            # ``create_all`` only creates *missing tables*; it never touches an
            # existing table's columns. Without this, adding a new column to
            # an ORM model silently breaks every SELECT against that table on
            # an upgraded production DB (asyncpg raises ``UndefinedColumnError``
            # on first query). Run a defensive ``ALTER TABLE ... ADD COLUMN``
            # for any column declared in the metadata that's missing on disk.
            await conn.run_sync(self._add_missing_columns)
        log.info("schema_applied")

    @staticmethod
    def _add_missing_columns(sync_conn) -> None:
        inspector = inspect(sync_conn)
        existing_tables = set(inspector.get_table_names())
        dialect = sync_conn.dialect
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                # Use SQLAlchemy's CreateColumn DDL so types/defaults match the
                # ORM declaration. Adding a NOT NULL column to a non-empty
                # table without a server_default would fail; we relax that to
                # NULL on the fly so the migration can still run, and let the
                # application fill the column going forward.
                col_ddl = CreateColumn(col).compile(dialect=dialect).string
                if "NOT NULL" in col_ddl.upper() and col.server_default is None:
                    col_ddl = col_ddl.replace("NOT NULL", "").strip()
                sync_conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {col_ddl}")
                log.info("schema_column_added", table=table.name, column=col.name)

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
