"""Issue 1 — repair fills update realized_pnl_quote.
Issue 3 — repair refuses when projected post-repair PnL is below
``-max_repair_loss_quote``.
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
from app.execution.paper_fill_engine import PaperFillConfig, PaperFillEngine
from app.execution.repair_engine import RepairEngine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.opportunity import ArbitrageOpportunity
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.exposure_manager import ExposureManager


def _opp() -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        opportunity_id="opp_pnl",
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
        detected_at=datetime.now(timezone.utc),
    )


def _settings(**kw) -> Settings:
    base = dict(
        mode="paper-trade",
        enabled_symbols="BTC/USDT",
        max_repair_attempts=3,
        ioc_price_buffer_bps=Decimal("0"),
        max_book_age_ms_for_trade=999999,
        max_signal_to_order_ms=999999,
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )
    base.update(kw)
    return Settings(**base)


@pytest.mark.asyncio
async def test_repair_fill_is_folded_into_realized_pnl():
    """Buy book on ``a`` only has 0.2 base total -> buy partial-fills.
    sell fills 1.0 -> residual short 0.8. Repair buys 0.2 each attempt
    (book is static in the mock). After 3 attempts (max), 0.6 has been
    bought back, leaving 0.2 short. Realized PnL must include each
    repair fill's cost; without the fix, PnL stayed at the original
    leg-only number (~+81 quote), which silently inflated profitability."""
    settings = _settings(max_repair_loss_quote=Decimal("100000"))  # disable cap
    a = MockExchangeAdapter("a", fee_bps=Decimal("0"))
    b = MockExchangeAdapter("b", fee_bps=Decimal("0"))
    a.set_orderbook("BTC/USDT", bids=[(99, 10)], asks=[(100, 0.2)])
    b.set_orderbook("BTC/USDT", bids=[(101, 10)], asks=[(102, 10)])
    reg = AdapterRegistry([a, b])
    books = OrderBookManager(max_stale_ms=999999)
    books.update(await a.watch_orderbook("BTC/USDT"))
    books.update(await b.watch_orderbook("BTC/USDT"))
    bm = BalanceManager(reg)
    bm.set_virtual_balance("a", "USDT", Decimal("100000"))
    bm.set_virtual_balance("b", "BTC", Decimal("100"))

    paper = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    tracker = OrderTracker()
    router = OrderRouter(settings, reg, paper)
    repair = RepairEngine(settings, router, tracker, books)
    hedge = HedgeCoordinator(settings, router, tracker, repair, ExposureManager())

    group = await hedge.execute(_opp(), approved_amount=Decimal("1"))

    # Repair must have fired (at least once).
    assert group.repair_attempts >= 1
    assert group.realized_pnl_quote is not None
    # Pre-fix PnL would be 1.0*101 - 0.2*100 = 81. Each repair attempt
    # adds another -0.2*100 = -20. With 3 attempts we expect realized
    # to be in the (81 - 60) = 21 region. Strictly: must be < 81 with the
    # repair fold; without the fold it would stay at 81.
    assert group.realized_pnl_quote < Decimal("81")
    # Repair cost field is negative (each attempt was a buy).
    assert group.repair_cost_quote < Decimal(0)


@pytest.mark.asyncio
async def test_max_repair_loss_skip_protects_from_unbounded_loss():
    """Issue 3 — when the residual flatten would push net PnL deeper
    than -max_repair_loss_quote, repair is skipped and the group stays
    in FAILED_NEEDS_REPAIR rather than being force-flattened at terrible
    prices.

    Setup: the sell leg blows up entirely (no proceeds). Buy fills 1.0
    at 100. Rollback PnL = -100 quote. ``b``'s bids are awful (50), so a
    repair sell of 1.0 base would only get +50 → projected final PnL
    around -50. With ``max_repair_loss_quote=1`` we should refuse.
    """
    settings = _settings(max_repair_loss_quote=Decimal("1"))
    a = MockExchangeAdapter("a", fee_bps=Decimal("0"))
    b = MockExchangeAdapter("b", fee_bps=Decimal("0"))
    a.set_orderbook("BTC/USDT", bids=[(99, 10)], asks=[(100, 10)])
    b.set_orderbook("BTC/USDT", bids=[(50, 10)], asks=[(102, 10)])
    reg = AdapterRegistry([a, b])
    books = OrderBookManager(max_stale_ms=999999)
    books.update(await a.watch_orderbook("BTC/USDT"))
    books.update(await b.watch_orderbook("BTC/USDT"))
    bm = BalanceManager(reg)
    bm.set_virtual_balance("a", "USDT", Decimal("100000"))
    bm.set_virtual_balance("a", "BTC", Decimal("100"))
    bm.set_virtual_balance("b", "USDT", Decimal("100000"))
    bm.set_virtual_balance("b", "BTC", Decimal("100"))

    paper = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    tracker = OrderTracker()
    router = _SellFailingRouter(settings, reg, paper)
    repair = RepairEngine(settings, router, tracker, books)
    hedge = HedgeCoordinator(settings, router, tracker, repair, ExposureManager())

    group = await hedge.execute(_opp(), approved_amount=Decimal("1"))

    # The cap refuses the repair; the group is left for the operator.
    assert group.state == HedgeState.FAILED_NEEDS_REPAIR
    assert group.failure_reason == "repair_skipped_max_loss"
    assert group.repair_attempts == 0  # never actually submitted
    # Realized PnL should still be the surviving buy-leg cost (issue 1
    # rollback path), not None.
    assert group.realized_pnl_quote is not None
    assert group.realized_pnl_quote < Decimal(0)


class _SellFailingRouter(OrderRouter):
    async def submit(self, intent):
        if intent.side.value == "sell" and not intent.is_repair:
            raise TransientError("simulated sell venue outage")
        return self._paper.simulate(intent)


@pytest.mark.asyncio
async def test_rollback_path_records_realized_pnl_not_none():
    """Issue 1 — when the sell leg fails outright, the surviving buy
    leg's cost (and the eventual repair sell's proceeds) must end up in
    ``realized_pnl_quote``. Pre-fix this stayed ``None`` even though we
    had really traded."""
    settings = _settings(max_repair_loss_quote=Decimal("100000"))
    a = MockExchangeAdapter("a", fee_bps=Decimal("0"))
    b = MockExchangeAdapter("b", fee_bps=Decimal("0"))
    a.set_orderbook("BTC/USDT", bids=[(99, 10)], asks=[(100, 10)])
    b.set_orderbook("BTC/USDT", bids=[(101, 10)], asks=[(102, 10)])
    reg = AdapterRegistry([a, b])
    books = OrderBookManager(max_stale_ms=999999)
    books.update(await a.watch_orderbook("BTC/USDT"))
    books.update(await b.watch_orderbook("BTC/USDT"))
    bm = BalanceManager(reg)
    bm.set_virtual_balance("a", "USDT", Decimal("100000"))
    bm.set_virtual_balance("a", "BTC", Decimal("100"))
    bm.set_virtual_balance("b", "USDT", Decimal("100000"))
    bm.set_virtual_balance("b", "BTC", Decimal("100"))

    paper = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    tracker = OrderTracker()
    router = _SellFailingRouter(settings, reg, paper)
    repair = RepairEngine(settings, router, tracker, books)
    breaker = CircuitBreaker(max_consecutive_losing_trades=999)
    hedge = HedgeCoordinator(
        settings, router, tracker, repair, ExposureManager(), breaker=breaker, registry=reg
    )

    group = await hedge.execute(_opp(), approved_amount=Decimal("0.5"))

    # The hedge ended either completed via repair or in failed/aborted —
    # either way realized_pnl_quote must be a Decimal, not None.
    assert group.realized_pnl_quote is not None
    assert group.failure_reason is not None
