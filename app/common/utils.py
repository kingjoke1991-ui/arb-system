"""Misc utilities."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Iterable


def to_decimal(x: str | float | int | Decimal) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def quantize_down(x: Decimal, places: int) -> Decimal:
    q = Decimal(10) ** -places
    return x.quantize(q, rounding=ROUND_DOWN)


def quantize_half_up(x: Decimal, places: int) -> Decimal:
    q = Decimal(10) ** -places
    return x.quantize(q, rounding=ROUND_HALF_UP)


def bps_to_factor(bps: Decimal | int | float) -> Decimal:
    return to_decimal(bps) / Decimal("10000")


def factor_to_bps(factor: Decimal | int | float) -> Decimal:
    return to_decimal(factor) * Decimal("10000")


def clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def min_nonneg(values: Iterable[Decimal]) -> Decimal:
    vs = [v for v in values if v is not None]
    if not vs:
        return Decimal(0)
    return min(vs)
