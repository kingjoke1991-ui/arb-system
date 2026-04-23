from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    size: Decimal


@dataclass(slots=True)
class OrderBookSnapshot:
    exchange: str
    symbol: str  # canonical
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    ts_local: datetime
    ts_exchange: datetime | None = None
    latency_ms: int | None = None
    sequence_id: str | None = None

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Decimal | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None:
            return None
        return (bb + ba) / Decimal(2)

    def top_bid_size(self) -> Decimal:
        return self.bids[0].size if self.bids else Decimal(0)

    def top_ask_size(self) -> Decimal:
        return self.asks[0].size if self.asks else Decimal(0)
