"""
Slippage model: VWAP across top-N book levels.
Returns (vwap_price, fillable_size, slippage_bps_vs_top).
"""

from __future__ import annotations

from decimal import Decimal

from app.models.orderbook import OrderBookSnapshot


def vwap_buy(book: OrderBookSnapshot, target_base: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Buy `target_base` by walking asks from the top."""
    if not book.asks or target_base <= 0:
        return (Decimal(0), Decimal(0), Decimal(0))
    remaining = target_base
    cost = Decimal(0)
    filled = Decimal(0)
    for lvl in book.asks:
        take = min(remaining, lvl.size)
        cost += take * lvl.price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled == 0:
        return (Decimal(0), Decimal(0), Decimal(0))
    vwap = cost / filled
    top = book.asks[0].price
    slippage_bps = (vwap - top) / top * Decimal("10000") if top > 0 else Decimal(0)
    return (vwap, filled, slippage_bps)


def vwap_sell(book: OrderBookSnapshot, target_base: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Sell `target_base` by walking bids from the top."""
    if not book.bids or target_base <= 0:
        return (Decimal(0), Decimal(0), Decimal(0))
    remaining = target_base
    proceeds = Decimal(0)
    filled = Decimal(0)
    for lvl in book.bids:
        take = min(remaining, lvl.size)
        proceeds += take * lvl.price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled == 0:
        return (Decimal(0), Decimal(0), Decimal(0))
    vwap = proceeds / filled
    top = book.bids[0].price
    slippage_bps = (top - vwap) / top * Decimal("10000") if top > 0 else Decimal(0)
    return (vwap, filled, slippage_bps)


def max_fillable_buy(book: OrderBookSnapshot, depth_levels: int = 5) -> Decimal:
    return sum((lvl.size for lvl in book.asks[:depth_levels]), Decimal(0))


def max_fillable_sell(book: OrderBookSnapshot, depth_levels: int = 5) -> Decimal:
    return sum((lvl.size for lvl in book.bids[:depth_levels]), Decimal(0))
