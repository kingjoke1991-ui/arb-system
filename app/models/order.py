from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.common.enums import OrderStatus, OrderType, Side


@dataclass(slots=True)
class OrderIntent:
    hedge_group_id: str
    exchange: str
    symbol: str
    side: Side
    order_type: OrderType
    amount: Decimal
    price: Decimal | None = None
    time_in_force: str | None = None
    reduce_only: bool = False
    client_order_id: str | None = None
    is_repair: bool = False


@dataclass(slots=True)
class UnifiedOrderState:
    internal_order_id: str
    hedge_group_id: str
    exchange: str
    symbol: str
    side: Side
    price: Decimal | None
    amount: Decimal
    filled: Decimal
    remaining: Decimal
    avg_fill_price: Decimal | None
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    exchange_order_id: str | None = None
    client_order_id: str | None = None
    fee_amount: Decimal | None = None
    fee_asset: str | None = None
    is_repair: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
