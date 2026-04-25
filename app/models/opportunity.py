from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True)
class ArbitrageOpportunity:
    opportunity_id: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: Decimal
    sell_price: Decimal
    gross_spread_bps: Decimal
    buy_fee_bps: Decimal
    sell_fee_bps: Decimal
    slippage_bps: Decimal
    buffer_bps: Decimal
    net_edge_bps: Decimal
    max_tradable_size: Decimal  # in base asset
    expected_profit_quote: Decimal
    detected_at: datetime
    market_snapshot_ref: str | None = None
    decision: str | None = None
    decision_reason: str | None = None
    # Age of the underlying orderbook snapshots at detection time. Persisted
    # to the opportunities table and used post-mortem to correlate slippage
    # against book-age. Risk gating uses these to enforce
    # ``max_book_age_ms_for_trade``.
    buy_book_age_ms: int | None = None
    sell_book_age_ms: int | None = None
