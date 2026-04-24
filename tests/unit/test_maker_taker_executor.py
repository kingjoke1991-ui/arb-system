"""Maker-taker execution unit tests. Covers:

1) happy path — maker posts, ask drops to maker price, fills; hedge taker fires and closes
2) timeout — ask never drops; maker expires after max_wait_ms → CANCELED, no exposure
3) price drift — mid moves away past max_price_deviation_bps → CANCELED
4) partial fill below min_fill_ratio → CANCELED
5) hedge timeout → PANIC_CLOSING → FLAT
6) dry-run mode returns None (falls back to audit path)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.common.enums import OrderStatus, Side
from app.config.settings import Settings
from app.execution.maker_taker_executor import MakerTakerExecutor
from app.models.opportunity import ArbitrageOpportunity
from app.models.order import UnifiedOrderState
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot


class FakeBookMgr:
    """Scriptable orderbook provider for the executor to poll."""

    def __init__(self):
        self._script: dict[tuple[str, str], list[OrderBookSnapshot]] = {}

    def set_script(self, exchange: str, symbol: str, snaps: list[OrderBookSnapshot]) -> None:
        self._script[(exchange, symbol)] = list(snaps)

    def get(self, exchange: str, symbol: str) -> OrderBookSnapshot | None:
        lst = self._script.get((exchange, symbol))
        if not lst:
            return None
        # Consume sequentially but hold last value after list is drained
        if len(lst) == 1:
            return lst[0]
        return lst.pop(0)


class FakeRouter:
    def __init__(
        self, hedge_result: UnifiedOrderState | None = None, delay_s: float = 0.0, raise_error: bool = False
    ):
        self._hedge_result = hedge_result
        self._delay_s = delay_s
        self._raise = raise_error
        self.submitted: list = []

    async def submit(self, intent):
        self.submitted.append(intent)
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._raise:
            raise RuntimeError("submit failure")
        if self._hedge_result is None:
            # default: full fill at the requested price
            return UnifiedOrderState(
                internal_order_id="fake",
                hedge_group_id=intent.hedge_group_id,
                exchange=intent.exchange,
                symbol=intent.symbol,
                side=intent.side,
                price=intent.price,
                amount=intent.amount,
                filled=intent.amount,
                remaining=Decimal(0),
                avg_fill_price=intent.price,
                status=OrderStatus.FILLED,
                created_at=datetime.now(tz=timezone.utc),
                updated_at=datetime.now(tz=timezone.utc),
            )
        return self._hedge_result


def _book(exchange: str, symbol: str, bid: str, ask: str, size: str = "100") -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange=exchange,
        symbol=symbol,
        bids=[OrderBookLevel(price=Decimal(bid), size=Decimal(size))],
        asks=[OrderBookLevel(price=Decimal(ask), size=Decimal(size))],
        ts_local=datetime.now(tz=timezone.utc),
    )


def _opp(buy_price="100.0", sell_price="100.5") -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        opportunity_id="opp-1",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price=Decimal(buy_price),
        sell_price=Decimal(sell_price),
        gross_spread_bps=Decimal("50"),
        buy_fee_bps=Decimal("10"),
        sell_fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        buffer_bps=Decimal("0"),
        net_edge_bps=Decimal("30"),
        max_tradable_size=Decimal("1"),
        expected_profit_quote=Decimal("0.5"),
        detected_at=datetime.now(tz=timezone.utc),
    )


def _settings(**overrides) -> Settings:
    kw = {
        "mode": "paper-trade",
        "execution_mode": "maker_taker",
        "maker_offset_bps": Decimal("1"),
        "min_fill_ratio": Decimal("0.5"),
        "max_wait_ms": 500,
        "hedge_timeout_ms": 500,
        "max_price_deviation_bps": Decimal("10"),
        "maker_poll_interval_ms": 20,
    }
    kw.update(overrides)
    return Settings(**kw)


@pytest.mark.asyncio
async def test_happy_path_full_fill():
    """Maker posts; ask drops to maker price; hedge fires; DONE."""
    bm = FakeBookMgr()
    # Initial book: bid 100, ask 100.05 (maker posts at 100.01 = best_bid*(1+1bps))
    # Second snap: ask drops to 100.005 — below maker_price — simulated fill.
    bm.set_script(
        "binance",
        "BTC/USDT",
        [
            _book("binance", "BTC/USDT", "100", "100.05", size="5"),
            _book("binance", "BTC/USDT", "100", "100.005", size="5"),
        ],
    )
    bm.set_script("okx", "BTC/USDT", [_book("okx", "BTC/USDT", "100.5", "100.55", size="5")])
    router = FakeRouter()
    ex = MakerTakerExecutor(_settings(), router, bm)

    rep = await ex.execute(_opp(), Decimal("1"))
    assert rep is not None
    assert rep.state == "DONE"
    assert rep.filled_amount == Decimal("1")
    assert rep.hedge_filled_amount == Decimal("1")
    # Hedge intent routed through OrderRouter
    assert len(router.submitted) == 1
    assert router.submitted[0].side == Side.SELL
    assert router.submitted[0].exchange == "okx"


@pytest.mark.asyncio
async def test_timeout_no_fill():
    """Maker never fills within max_wait_ms → CANCELED."""
    bm = FakeBookMgr()
    # Ask stays far above maker_price forever.
    bm.set_script("binance", "BTC/USDT", [_book("binance", "BTC/USDT", "100", "100.5")])
    bm.set_script("okx", "BTC/USDT", [_book("okx", "BTC/USDT", "100.5", "101")])
    router = FakeRouter()
    ex = MakerTakerExecutor(_settings(max_wait_ms=200), router, bm)

    rep = await ex.execute(_opp(), Decimal("1"))
    assert rep is not None
    assert rep.state == "CANCELED"
    assert rep.reason == "max_wait_ms"
    # Hedge never fired
    assert len(router.submitted) == 0


@pytest.mark.asyncio
async def test_price_drift_cancels():
    """Mid drifts past max_price_deviation_bps → CANCELED."""
    bm = FakeBookMgr()
    # Second snapshot: mid jumps from 100.025 → 101 (100+ bps drift), exceeds tolerance.
    bm.set_script(
        "binance",
        "BTC/USDT",
        [
            _book("binance", "BTC/USDT", "100", "100.05", size="5"),
            _book("binance", "BTC/USDT", "101", "101.05", size="5"),
        ],
    )
    bm.set_script("okx", "BTC/USDT", [_book("okx", "BTC/USDT", "100.5", "101")])
    router = FakeRouter()
    ex = MakerTakerExecutor(_settings(max_price_deviation_bps=Decimal("5")), router, bm)

    rep = await ex.execute(_opp(), Decimal("1"))
    assert rep is not None
    assert rep.state == "CANCELED"
    assert rep.reason == "price_drift_exceeded"
    assert len(router.submitted) == 0


@pytest.mark.asyncio
async def test_partial_below_min_fill_ratio():
    """Only partial depth available, ratio < min_fill_ratio → CANCELED."""
    bm = FakeBookMgr()
    # Ask drops but only 0.1 of depth at price, far below 1.0 requested.
    bm.set_script(
        "binance",
        "BTC/USDT",
        [
            _book("binance", "BTC/USDT", "100", "100.05", size="5"),
            OrderBookSnapshot(
                exchange="binance",
                symbol="BTC/USDT",
                bids=[OrderBookLevel(price=Decimal("100"), size=Decimal("5"))],
                asks=[OrderBookLevel(price=Decimal("100.005"), size=Decimal("0.1"))],
                ts_local=datetime.now(tz=timezone.utc),
            ),
        ],
    )
    bm.set_script("okx", "BTC/USDT", [_book("okx", "BTC/USDT", "100.5", "101")])
    router = FakeRouter()
    ex = MakerTakerExecutor(_settings(min_fill_ratio=Decimal("0.5")), router, bm)

    rep = await ex.execute(_opp(), Decimal("1"))
    assert rep is not None
    assert rep.state == "CANCELED"
    assert "fill_ratio_below_min" in rep.reason
    assert len(router.submitted) == 0


@pytest.mark.asyncio
async def test_hedge_timeout_triggers_panic_close():
    """Maker fills but hedge taker times out → PANIC_CLOSING → FLAT."""
    bm = FakeBookMgr()
    bm.set_script(
        "binance",
        "BTC/USDT",
        [
            _book("binance", "BTC/USDT", "100", "100.05", size="5"),
            _book("binance", "BTC/USDT", "100", "100.005", size="5"),
        ],
    )
    bm.set_script("okx", "BTC/USDT", [_book("okx", "BTC/USDT", "100.5", "101", size="5")])

    # First router call is the hedge (too slow); subsequent calls are the panic-close (fast).
    call_count = {"n": 0}

    class FlakyRouter:
        def __init__(self):
            self.submitted: list = []

        async def submit(self, intent):
            self.submitted.append(intent)
            call_count["n"] += 1
            if call_count["n"] == 1:
                await asyncio.sleep(2.0)  # > hedge_timeout_ms
                # Won't return in time
            # Panic close (call #2) succeeds immediately
            return UnifiedOrderState(
                internal_order_id="fake",
                hedge_group_id=intent.hedge_group_id,
                exchange=intent.exchange,
                symbol=intent.symbol,
                side=intent.side,
                price=intent.price,
                amount=intent.amount,
                filled=intent.amount,
                remaining=Decimal(0),
                avg_fill_price=intent.price,
                status=OrderStatus.FILLED,
                created_at=datetime.now(tz=timezone.utc),
                updated_at=datetime.now(tz=timezone.utc),
            )

    router = FlakyRouter()
    ex = MakerTakerExecutor(_settings(hedge_timeout_ms=200), router, bm)
    rep = await ex.execute(_opp(), Decimal("1"))
    assert rep is not None
    assert rep.state == "FLAT"
    assert rep.reason == "hedge_timeout_panic_closed"
    # Both hedge and panic-close intents were submitted
    assert len(router.submitted) == 2
    assert router.submitted[0].side == Side.SELL  # hedge
    assert router.submitted[1].is_repair  # panic close


@pytest.mark.asyncio
async def test_dry_run_returns_none():
    """dry-run short-circuits; returns None so upstream uses audit path."""
    bm = FakeBookMgr()
    router = FakeRouter()
    ex = MakerTakerExecutor(_settings(mode="dry-run"), router, bm)
    rep = await ex.execute(_opp(), Decimal("1"))
    assert rep is None
    assert len(router.submitted) == 0


@pytest.mark.asyncio
async def test_missing_book_returns_canceled():
    """No orderbook snapshot → immediate CANCELED with reason."""
    bm = FakeBookMgr()  # no scripts set → get() returns None
    router = FakeRouter()
    ex = MakerTakerExecutor(_settings(), router, bm)
    rep = await ex.execute(_opp(), Decimal("1"))
    assert rep is not None
    assert rep.state == "CANCELED"
    assert rep.reason == "missing_orderbook_snapshot"
