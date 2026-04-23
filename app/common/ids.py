"""ID generation utilities. All ids are string, prefixed and globally unique."""

from __future__ import annotations

import secrets
import time


def _uid(length: int = 10) -> str:
    return secrets.token_hex(length // 2 + 1)[:length]


def _ts_part() -> str:
    return f"{int(time.time() * 1000):013d}"


def new_opportunity_id() -> str:
    return f"opp_{_ts_part()}_{_uid(8)}"


def new_hedge_group_id() -> str:
    return f"hg_{_ts_part()}_{_uid(8)}"


def new_order_id() -> str:
    return f"ord_{_ts_part()}_{_uid(8)}"


def new_snapshot_id() -> str:
    return f"snap_{_ts_part()}_{_uid(6)}"


def new_client_order_id(hedge_group_id: str, side: str) -> str:
    """Short, unique client order id (idempotency key for exchange submit)."""
    return f"{hedge_group_id[-12:]}-{side[0]}-{_uid(4)}"
