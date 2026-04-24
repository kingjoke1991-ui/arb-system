"""Regression for C1/C2/C3/C4.

Specifically verifies that when one leg of a parallel hedge submission
raises an exception:
  * the survivor leg is cancelled (or routed to repair if already filled)
  * the circuit breaker records the failure
  * exposure is released
  * paper-trade balances are actually adjusted
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.accounts.balance_manager import BalanceManager
from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.common.enums import HedgeState
from app.common.exceptions import TransientError
from app.config.settings import Settings
from app.execution.hedge_coordinator import HedgeCoordinator
from app.execution.order_router import OrderRouter
from app.execution.order_tracker import OrderTracker
from app.execution.paper_fill_engine import PaperFillEngine
from app.execution.repair_engine import RepairEngine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.opportunity import ArbitrageOpportunity
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.exposure_manager import ExposureManager


def _make_opp() -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        opportunity_id="op_test",
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
        net_edge_bps=Decimal("80"),
        max_tradable_size=Decimal("0.5"),
        expected_profit_quote=Decimal("0.5"),
        detected_at=datetime.now(timezone.utc),
    )


class _FailingRouter(OrderRouter):
    """Router that always rejects SELL orders, letting BUY orders fill
    normally via the paper engine. Simulates a one-leg exchange outage."""

    async def submit(self, intent):
        if intent.side.value == "sell":
            raise TransientError("simulated sell venue outage")
        return self._paper.simulate(intent)


async def _build_system():
    settings = Settings(
        mode="paper-trade",
        enabled_symbols="BTC/USDT",
        min_net_edge_bps=Decimal("0"),
        min_profit_quote=Decimal("0"),
        min_order_size_quote=Decimal("0"),
        max_notional_per_trade=Decimal("100"),
        max_exposure_per_exchange=Decimal("1000"),
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
    bm.set_virtual_balance("a", "BTC", Decimal("1"))
    bm.set_virtual_balance("b", "USDT", Decimal("10000"))
    bm.set_virtual_balance("b", "BTC", Decimal("1"))

    paper = PaperFillEngine(books, balance_mgr=bm)
    tracker = OrderTracker()
    router = _FailingRouter(settings, reg, paper)
    repair = RepairEngine(settings, router, tracker, books)
    breaker = CircuitBreaker()
    exposure = ExposureManager()
    hedge = HedgeCoordinator(
        settings,
        router,
        tracker,
        repair,
        exposure,
        breaker=breaker,
        registry=reg,
    )
    return {
        "settings": settings,
        "hedge": hedge,
        "breaker": breaker,
        "exposure": exposure,
        "balance_mgr": bm,
    }


@pytest.mark.asyncio
async def test_circuit_breaker_records_partial_leg_failure():
    """C1: One leg failing should be fed into the circuit breaker."""
    sys = await _build_system()
    group = await sys["hedge"].execute(_make_opp(), Decimal("0.5"))
    # Sell side raises. Buy side fills. Survivor buy tries to get cancelled
    # (can't in paper, paper fills are instant) then goes to repair.
    assert group.state in (
        HedgeState.FAILED_NEEDS_REPAIR,
        HedgeState.ABORTED,
        HedgeState.REPAIRING,
    )
    # Circuit breaker saw at least one failure + one success.
    assert sys["breaker"].status()["window_samples"] >= 2


@pytest.mark.asyncio
async def test_paper_virtual_balance_debited_on_buy_fill():
    """C3: BUY leg in paper mode must actually debit virtual USDT
    and credit BTC."""
    # Use a working router (not _FailingRouter) so both legs succeed.
    settings = Settings(
        mode="paper-trade",
        enabled_symbols="BTC/USDT",
        min_net_edge_bps=Decimal("0"),
        min_profit_quote=Decimal("0"),
        min_order_size_quote=Decimal("0"),
        max_notional_per_trade=Decimal("100"),
        max_exposure_per_exchange=Decimal("1000"),
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
    bm.set_virtual_balance("a", "BTC", Decimal("1"))
    bm.set_virtual_balance("b", "USDT", Decimal("10000"))
    bm.set_virtual_balance("b", "BTC", Decimal("1"))
    paper = PaperFillEngine(books, balance_mgr=bm)
    tracker = OrderTracker()
    router = OrderRouter(settings, reg, paper)
    repair = RepairEngine(settings, router, tracker, books)
    exposure = ExposureManager()
    hedge = HedgeCoordinator(
        settings,
        router,
        tracker,
        repair,
        exposure,
        breaker=CircuitBreaker(),
        registry=reg,
    )

    before_a_usdt = bm.free("a", "USDT")
    before_a_btc = bm.free("a", "BTC")
    before_b_btc = bm.free("b", "BTC")

    await hedge.execute(_make_opp(), Decimal("0.5"))

    after_a_usdt = bm.free("a", "USDT")
    after_a_btc = bm.free("a", "BTC")
    after_b_btc = bm.free("b", "BTC")
    # Buy on a: USDT decreases, BTC increases.
    assert after_a_usdt < before_a_usdt, (
        f"virtual USDT on exchange a should decrease after buy; before={before_a_usdt} after={after_a_usdt}"
    )
    assert after_a_btc > before_a_btc
    # Sell on b: BTC decreases.
    assert after_b_btc < before_b_btc


@pytest.mark.asyncio
async def test_exposure_released_after_failure():
    """C4: Exposure must be released regardless of leg outcome."""
    sys = await _build_system()
    await sys["hedge"].execute(_make_opp(), Decimal("0.5"))
    assert sys["exposure"].in_flight("a") == Decimal("0")
    assert sys["exposure"].in_flight("b") == Decimal("0")
