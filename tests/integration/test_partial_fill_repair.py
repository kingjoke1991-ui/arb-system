"""When one leg partial-fills, the hedge coordinator should invoke the repair engine."""

from decimal import Decimal

import pytest

from app.accounts.balance_manager import BalanceManager
from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.common.enums import HedgeState
from app.config.settings import Settings
from app.execution.hedge_coordinator import HedgeCoordinator
from app.execution.order_router import OrderRouter
from app.execution.order_tracker import OrderTracker
from app.execution.paper_fill_engine import PaperFillConfig, PaperFillEngine
from app.execution.repair_engine import RepairEngine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.opportunity import ArbitrageOpportunity
from app.risk.exposure_manager import ExposureManager


@pytest.mark.asyncio
async def test_partial_fill_triggers_repair():
    settings = Settings(
        mode="paper-trade",
        enabled_symbols="BTC/USDT",
        max_repair_attempts=3,
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )
    a = MockExchangeAdapter("a")
    b = MockExchangeAdapter("b")
    # Make sell side deep, buy side shallow to force partial fill on buy leg.
    a.set_orderbook("BTC/USDT", bids=[(99, 1)], asks=[(100, 0.2)])  # only 0.2 available
    b.set_orderbook("BTC/USDT", bids=[(101, 10)], asks=[(102, 10)])
    reg = AdapterRegistry([a, b])

    books = OrderBookManager(max_stale_ms=999999)
    books.update(await a.watch_orderbook("BTC/USDT"))
    books.update(await b.watch_orderbook("BTC/USDT"))

    bm = BalanceManager(reg)
    bm.set_virtual_balance("a", "USDT", Decimal("10000"))
    bm.set_virtual_balance("b", "BTC", Decimal("10"))

    paper = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    tracker = OrderTracker()
    router = OrderRouter(settings, reg, paper)
    repair = RepairEngine(settings, router, tracker, books)
    hedge = HedgeCoordinator(settings, router, tracker, repair, ExposureManager())

    opp = ArbitrageOpportunity(
        opportunity_id="opp_x",
        symbol="BTC/USDT",
        buy_exchange="a",
        sell_exchange="b",
        buy_price=Decimal("100"),
        sell_price=Decimal("101"),
        gross_spread_bps=Decimal("100"),
        buy_fee_bps=Decimal("1"),
        sell_fee_bps=Decimal("1"),
        slippage_bps=Decimal("0"),
        buffer_bps=Decimal("0"),
        net_edge_bps=Decimal("90"),
        max_tradable_size=Decimal("1"),
        expected_profit_quote=Decimal("1"),
        detected_at=__import__("datetime").datetime.utcnow(),
    )
    group = await hedge.execute(opp, approved_amount=Decimal("1"))
    # Buy can only fill 0.2 on mock book -> repair should be invoked.
    assert group.repair_attempts >= 1
    # Repair brings net to near zero or aborts after attempts
    assert group.state in (HedgeState.COMPLETED, HedgeState.ABORTED, HedgeState.FAILED_NEEDS_REPAIR)
