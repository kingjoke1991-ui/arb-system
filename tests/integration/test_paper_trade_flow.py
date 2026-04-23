"""End-to-end: scanner -> risk -> paper-trade execution."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.accounts.balance_manager import BalanceManager
from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.common.enums import HedgeState, Mode
from app.config.settings import Settings
from app.execution.hedge_coordinator import HedgeCoordinator
from app.execution.order_router import OrderRouter
from app.execution.order_tracker import OrderTracker
from app.execution.paper_fill_engine import PaperFillEngine
from app.execution.repair_engine import RepairEngine
from app.marketdata.orderbook_manager import OrderBookManager
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.exposure_manager import ExposureManager
from app.risk.health_guard import HealthGuard
from app.risk.kill_switch import KillSwitch
from app.risk.rules import RiskEngine
from app.strategy.fee_model import FeeModel
from app.strategy.opportunity_scanner import OpportunityScanner
from app.strategy.spread_calculator import SpreadCalculator


@pytest.mark.asyncio
async def test_paper_trade_end_to_end():
    settings = Settings(
        mode="paper-trade",
        enabled_symbols="BTC/USDT",
        min_net_edge_bps=Decimal("5"),
        min_profit_quote=Decimal("0.01"),
        min_order_size_quote=Decimal("1"),
        max_notional_per_trade=Decimal("100"),
        max_exposure_per_exchange=Decimal("1000"),
        max_marketdata_staleness_ms=999999,
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )
    a = MockExchangeAdapter("a", fee_bps=Decimal("1"))
    b = MockExchangeAdapter("b", fee_bps=Decimal("1"))
    a.set_orderbook("BTC/USDT", bids=[(99, 1)], asks=[(100, 1)])
    b.set_orderbook("BTC/USDT", bids=[(101, 1)], asks=[(102, 1)])
    reg = AdapterRegistry([a, b])

    books = OrderBookManager(max_stale_ms=999999)
    books.update(await a.watch_orderbook("BTC/USDT"))
    books.update(await b.watch_orderbook("BTC/USDT"))

    bm = BalanceManager(reg)
    bm.set_virtual_balance("a", "USDT", Decimal("10000"))
    bm.set_virtual_balance("b", "BTC", Decimal("1"))

    fees = FeeModel(reg)
    calc = SpreadCalculator(fees, buffer_bps=Decimal("0"))
    kill = KillSwitch()
    breaker = CircuitBreaker()
    health = HealthGuard(books, bm, settings)
    exposure = ExposureManager()
    risk = RiskEngine(settings, kill, breaker, health, exposure, bm)

    paper = PaperFillEngine(books)
    tracker = OrderTracker()
    router = OrderRouter(settings, reg, paper)
    repair = RepairEngine(settings, router, tracker, books)
    hedge = HedgeCoordinator(settings, router, tracker, repair, exposure)
    scanner = OpportunityScanner(settings, books, bm, calc)

    opps = await scanner.scan_once(["a", "b"])
    assert opps, "expected at least one opportunity"

    # Pick the direction with positive edge
    good = [o for o in opps if o.buy_exchange == "a" and o.sell_exchange == "b"][0]
    decision = risk.evaluate(good)
    assert decision.approved, decision.reason

    group = await hedge.execute(good, decision.approved_amount)
    assert group.state in (HedgeState.COMPLETED, HedgeState.FAILED_NEEDS_REPAIR)
    # In the ideal mock case, fully filled
    assert group.executed_buy_amount > 0
    assert group.executed_sell_amount > 0
