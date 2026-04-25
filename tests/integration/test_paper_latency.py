"""Issue 4 — OrderRouter applies paper_fill_latency_ms before the paper
engine reads the book, so a book that drifts between scan and submit
will cause the paper fill to under-fill (matching live behavior)."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.common.enums import OrderType, Side
from app.config.settings import Settings
from app.execution.order_router import OrderRouter
from app.execution.paper_fill_engine import PaperFillConfig, PaperFillEngine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.order import OrderIntent
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot


def _book(asks: list[tuple[float, float]]):
    return OrderBookSnapshot(
        exchange="x",
        symbol="BTC/USDT",
        bids=[OrderBookLevel(Decimal("0"), Decimal("0"))],
        asks=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in asks],
        ts_local=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_router_applies_latency_and_picks_up_drifted_book():
    settings = Settings(
        mode="paper-trade",
        enabled_symbols="BTC/USDT",
        paper_fill_latency_ms=100,
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )
    a = MockExchangeAdapter("x")
    reg = AdapterRegistry([a])
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 1)]))  # scanner snapshot
    paper = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    router = OrderRouter(settings, reg, paper)
    intent = OrderIntent(
        hedge_group_id="hg",
        client_order_id="co",
        exchange="x",
        symbol="BTC/USDT",
        side=Side.BUY,
        order_type=OrderType.IOC_LIMIT,
        amount=Decimal("1.0"),
        price=Decimal("100"),
    )

    async def _drift():
        # Push a worse book in while the router is sleeping its latency.
        await asyncio.sleep(0.02)
        books.update(_book(asks=[(101, 1)]))

    drift_task = asyncio.create_task(_drift())
    state = await router.submit(intent)
    await drift_task

    # Limit 100, drifted book best ask 101 → no fill.
    assert state.filled == Decimal("0")


@pytest.mark.asyncio
async def test_router_no_latency_for_repair_legs():
    """Repair fills should be immediate — they're already a corrective
    action and adding more latency just delays the rebalance."""
    settings = Settings(
        mode="paper-trade",
        enabled_symbols="BTC/USDT",
        paper_fill_latency_ms=200,
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )
    reg = AdapterRegistry([MockExchangeAdapter("x")])
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 1)]))
    paper = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    router = OrderRouter(settings, reg, paper)
    intent = OrderIntent(
        hedge_group_id="hg",
        client_order_id="co",
        exchange="x",
        symbol="BTC/USDT",
        side=Side.BUY,
        order_type=OrderType.IOC_LIMIT,
        amount=Decimal("1.0"),
        price=Decimal("100"),
        is_repair=True,
    )
    import time

    t0 = time.monotonic()
    await router.submit(intent)
    elapsed = time.monotonic() - t0
    # Expect well under 200ms because is_repair=True bypasses latency.
    assert elapsed < 0.1
