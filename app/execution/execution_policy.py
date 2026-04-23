"""Per-leg execution policy: pricing, IOC buffer, cancel timeout, etc."""

from __future__ import annotations

from decimal import Decimal

from app.common.enums import OrderType, Side
from app.config.settings import Settings


def pick_order_type(settings: Settings) -> OrderType:
    return OrderType(settings.order_type_policy)


def protected_limit_price(settings: Settings, side: Side, reference_price: Decimal) -> Decimal:
    """Aggressive-protected price for IOC limit."""
    buffer_factor = settings.ioc_price_buffer_bps / Decimal("10000")
    if side == Side.BUY:
        return reference_price * (Decimal(1) + buffer_factor)
    return reference_price * (Decimal(1) - buffer_factor)
