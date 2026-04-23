"""Apply the DB schema (create tables if missing)."""

from __future__ import annotations

import asyncio

from app.common.logging import configure_logging, get_logger
from app.config.settings import get_settings
from app.storage.db import Database


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    log = get_logger("scripts.apply_schema")
    db = Database(s.postgres_dsn)
    try:
        await db.create_all()
        log.info("schema_applied", dsn=s.postgres_dsn.rsplit("@", 1)[-1])
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
