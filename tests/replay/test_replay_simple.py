"""Simple deterministic replay — given a sequence of orderbook snapshots,
the scanner should pick out the expected number of positive-edge opportunities.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.accounts.balance_manager import BalanceManager
from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.strategy.fee_model import FeeModel
from app.strategy.opportunity_scanner import OpportunityScanner
from app.strategy.spread_calculator import SpreadCalculator


def _snap(ex, bids, asks):
    return OrderBookSnapshot(
        exchange=ex,
        symbol="BTC/USDT",
        bids=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in bids],
        asks=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in asks],
        ts_local=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_replay_sequence():
    settings = Settings(
        mode="dry-run",
        enabled_symbols="BTC/USDT",
        min_net_edge_bps=Decimal("10"),
        min_profit_quote=Decimal("0.01"),
        min_order_size_quote=Decimal("1"),
        max_notional_per_trade=Decimal("100"),
        max_marketdata_staleness_ms=999999,
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )
    a = MockExchangeAdapter("a", fee_bps=Decimal("1"))
    b = MockExchangeAdapter("b", fee_bps=Decimal("1"))
    reg = AdapterRegistry([a, b])
    books = OrderBookManager(max_stale_ms=999999)
    bm = BalanceManager(reg)
    bm.set_virtual_balance("a", "USDT", Decimal("10000"))
    bm.set_virtual_balance("b", "BTC", Decimal("1"))

    fees = FeeModel(reg)
    calc = SpreadCalculator(fees, buffer_bps=Decimal("0"))
    scanner = OpportunityScanner(settings, books, bm, calc)

    # Three ticks: tick 1 has edge, tick 2 is flat, tick 3 has edge the other way
    ticks = [
        (
            _snap("a", bids=[(99, 1)], asks=[(100, 1)]),
            _snap("b", bids=[(101, 1)], asks=[(102, 1)]),
            1,  # expected opportunities
        ),
        (
            _snap("a", bids=[(100, 1)], asks=[(100.1, 1)]),
            _snap("b", bids=[(100, 1)], asks=[(100.1, 1)]),
            0,
        ),
        (
            _snap("a", bids=[(101, 1)], asks=[(102, 1)]),
            _snap("b", bids=[(99, 1)], asks=[(100, 1)]),
            1,
        ),
    ]
    for snap_a, snap_b, expected in ticks:
        books.update(snap_a)
        books.update(snap_b)
        opps = await scanner.scan_once(["a", "b"])
        assert len(opps) == expected, f"tick expected {expected}, got {len(opps)}"
