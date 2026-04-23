"""
Tracks in-flight exposure per exchange + open hedge groups.
"""

from __future__ import annotations

from decimal import Decimal


class ExposureManager:
    def __init__(self):
        # exchange -> notional quote in flight
        self._in_flight: dict[str, Decimal] = {}
        self._open_hedges: set[str] = set()

    def add(self, exchange: str, notional_quote: Decimal, hedge_group_id: str) -> None:
        self._in_flight[exchange] = self._in_flight.get(exchange, Decimal(0)) + notional_quote
        self._open_hedges.add(hedge_group_id)

    def release(self, exchange: str, notional_quote: Decimal, hedge_group_id: str) -> None:
        cur = self._in_flight.get(exchange, Decimal(0))
        self._in_flight[exchange] = max(Decimal(0), cur - notional_quote)
        self._open_hedges.discard(hedge_group_id)

    def in_flight(self, exchange: str) -> Decimal:
        return self._in_flight.get(exchange, Decimal(0))

    def open_count(self) -> int:
        return len(self._open_hedges)

    def snapshot(self) -> dict:
        return {
            "in_flight": {k: str(v) for k, v in self._in_flight.items()},
            "open_hedges": list(self._open_hedges),
        }
