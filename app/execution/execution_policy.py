"""Per-leg execution policy: pricing, IOC buffer, cancel timeout, etc."""

from __future__ import annotations

from decimal import Decimal

from app.common.enums import OrderType, Side
from app.config.settings import Settings
from app.models.orderbook import OrderBookSnapshot


def pick_order_type(settings: Settings) -> OrderType:
    return OrderType(settings.order_type_policy)


def protected_limit_price(settings: Settings, side: Side, reference_price: Decimal) -> Decimal:
    """Aggressive-protected price for IOC limit."""
    buffer_factor = settings.ioc_price_buffer_bps / Decimal("10000")
    if side == Side.BUY:
        return reference_price * (Decimal(1) + buffer_factor)
    return reference_price * (Decimal(1) - buffer_factor)


def in_band_depth(
    book: OrderBookSnapshot | None,
    side: Side,
    ioc_buffer_bps: Decimal,
) -> Decimal:
    """Cumulative base-asset depth that an IOC limit at the protected price
    can actually consume.

    For a BUY: walks the ask ladder, summing levels at price <= best_ask *
    (1 + buffer). For a SELL: walks the bid ladder, summing levels at
    price >= best_bid * (1 - buffer).

    This is the *real* fillable size for an IOC limit order — it is NOT
    the same as VWAP-fillable size, because VWAP averages across all
    levels regardless of price, whereas an IOC limit only matches levels
    that satisfy the limit price constraint. The scanner's expected
    ``max_tradable_base`` (computed from VWAP) routinely overestimates
    the IOC-fillable depth on thin venues by 50-100x — see hedge groups
    that filled 100% on the deep leg and <5% on the thin leg.
    """
    if book is None:
        return Decimal(0)
    buf = ioc_buffer_bps / Decimal("10000")
    if side == Side.BUY:
        best = book.best_ask
        if best is None:
            return Decimal(0)
        limit = best * (Decimal(1) + buf)
        return sum(
            (lvl.size for lvl in book.asks if lvl.price <= limit),
            Decimal(0),
        )
    best = book.best_bid
    if best is None:
        return Decimal(0)
    limit = best * (Decimal(1) - buf)
    return sum(
        (lvl.size for lvl in book.bids if lvl.price >= limit),
        Decimal(0),
    )
