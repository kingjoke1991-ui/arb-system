"""WebSocket-mode unit tests for OrderBookManager.

Exercises the WS-vs-REST routing inside ``_poll_one``: when an adapter
advertises ``supports_websocket`` the manager calls ``watch_orderbook_ws``
and tags the source as "ws"; when WS fails repeatedly the manager
falls back to REST and tags the source as "rest"; when the operator
sets ``marketdata_mode="rest"`` the manager skips WS entirely even if
the adapter supports it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.adapters.base import ExchangeAdapter
from app.common.exceptions import TransientError
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.balance import BalanceSnapshot
from app.models.order import OrderIntent, UnifiedOrderState
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot


def _snap(name: str, sym: str = "BTC/USDT", bid: str = "100", ask: str = "101") -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange=name,
        symbol=sym,
        bids=[OrderBookLevel(Decimal(bid), Decimal("1"))],
        asks=[OrderBookLevel(Decimal(ask), Decimal("1"))],
        ts_local=datetime.now(timezone.utc),
        ts_exchange=datetime.now(timezone.utc),
        latency_ms=5,
    )


class _StubAdapter(ExchangeAdapter):
    """In-memory adapter that lets each test control ws/rest behavior.

    ``ws_enabled``       — return value of supports_websocket()
    ``ws_seq``           — list of return values OR exceptions from
                            watch_orderbook_ws() in order
    ``rest_seq``         — same for watch_orderbook (REST)
    Counters ``ws_calls`` / ``rest_calls`` track invocations.
    """

    def __init__(
        self,
        name: str,
        ws_enabled: bool = True,
        ws_seq: list | None = None,
        rest_seq: list | None = None,
    ):
        self.name = name
        self._ws_enabled = ws_enabled
        self._ws_seq = list(ws_seq or [])
        self._rest_seq = list(rest_seq or [])
        self.ws_calls = 0
        self.rest_calls = 0

    @property
    def is_configured(self) -> bool:
        return False

    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    def supports_websocket(self) -> bool:
        return self._ws_enabled

    async def watch_orderbook_ws(self, symbol: str) -> OrderBookSnapshot:
        self.ws_calls += 1
        if not self._ws_seq:
            # Block forever once the test's planned sequence is exhausted so
            # the polling loop doesn't busy-loop while we observe state.
            await asyncio.sleep(3600)
            raise RuntimeError("unreachable")
        item = self._ws_seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def watch_orderbook(self, symbol: str) -> OrderBookSnapshot:
        self.rest_calls += 1
        if not self._rest_seq:
            await asyncio.sleep(3600)
            raise RuntimeError("unreachable")
        item = self._rest_seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def fetch_balances(self) -> list[BalanceSnapshot]:
        return []

    async def create_order(self, intent: OrderIntent) -> UnifiedOrderState:
        raise NotImplementedError

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> UnifiedOrderState:
        raise NotImplementedError

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> UnifiedOrderState:
        raise NotImplementedError

    def fee_rate(self, symbol: str, side: str) -> Decimal:
        return Decimal("0.001")


@pytest.mark.asyncio
async def test_ws_path_marks_source_and_skips_rest():
    """When ws is healthy the manager calls only watch_orderbook_ws."""
    adapter = _StubAdapter("binance", ws_enabled=True, ws_seq=[_snap("binance")])
    mgr = OrderBookManager(poll_interval_ms=10, marketdata_mode="auto")
    mgr._running = True
    task = asyncio.create_task(mgr._poll_one(adapter, "BTC/USDT"))
    # Let one ws update flow through, then cancel the loop.
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await mgr.stop()
    assert adapter.ws_calls >= 1
    assert adapter.rest_calls == 0
    assert mgr.data_source("binance", "BTC/USDT") == "ws"
    assert mgr.get("binance", "BTC/USDT") is not None


@pytest.mark.asyncio
async def test_rest_mode_skips_ws_even_when_supported():
    """marketdata_mode='rest' disables ws regardless of adapter capability."""
    adapter = _StubAdapter(
        "binance",
        ws_enabled=True,
        rest_seq=[_snap("binance")],
    )
    mgr = OrderBookManager(poll_interval_ms=10, marketdata_mode="rest")
    mgr._running = True
    task = asyncio.create_task(mgr._poll_one(adapter, "BTC/USDT"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await mgr.stop()
    assert adapter.ws_calls == 0
    assert adapter.rest_calls >= 1
    assert mgr.data_source("binance", "BTC/USDT") == "rest"


@pytest.mark.asyncio
async def test_ws_failure_falls_back_to_rest_after_3_errors():
    """Three consecutive ws errors trigger fallback to REST polling.

    The polling loop should mark the source as "rest" after the third
    failure and stop hammering ws until the cooldown expires.
    """
    adapter = _StubAdapter(
        "okx",
        ws_enabled=True,
        ws_seq=[
            TransientError("ws boom 1"),
            TransientError("ws boom 2"),
            TransientError("ws boom 3"),
        ],
        rest_seq=[_snap("okx")] * 50,  # plenty of REST replies
    )
    mgr = OrderBookManager(poll_interval_ms=5, marketdata_mode="auto")
    mgr._running = True
    task = asyncio.create_task(mgr._poll_one(adapter, "BTC/USDT"))
    # 3 ws failures: backoff sleeps 5+10+20 = 35ms. After fallback the
    # loop should make at least one REST call before we cancel.
    # Cooldown is 30 REST polls (~150ms at 5ms interval) so ws will be
    # retried within this window — but the ws_seq is exhausted then so
    # a 4th ws call would just block on asyncio.sleep(3600). We assert
    # >=3 (not ==3) for robustness.
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await mgr.stop()
    assert adapter.ws_calls >= 3, "all 3 ws failures should have been attempted"
    assert adapter.rest_calls >= 1, "REST should have taken over after fallback"
    assert mgr.data_source("okx", "BTC/USDT") == "rest"


@pytest.mark.asyncio
async def test_legacy_adapter_without_ws_uses_rest():
    """Adapter that returns False from supports_websocket() never hits WS."""
    adapter = _StubAdapter(
        "mock",
        ws_enabled=False,
        rest_seq=[_snap("mock")],
    )
    mgr = OrderBookManager(poll_interval_ms=10, marketdata_mode="auto")
    mgr._running = True
    task = asyncio.create_task(mgr._poll_one(adapter, "BTC/USDT"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await mgr.stop()
    assert adapter.ws_calls == 0
    assert adapter.rest_calls >= 1
    assert mgr.data_source("mock", "BTC/USDT") == "rest"


@pytest.mark.asyncio
async def test_data_sources_summary_aggregates_per_exchange():
    """data_sources_summary buckets ws/rest counts per exchange."""
    mgr = OrderBookManager()
    mgr._sources[("binance", "BTC/USDT")] = "ws"
    mgr._sources[("binance", "ETH/USDT")] = "ws"
    mgr._sources[("binance", "SOL/USDT")] = "rest"
    mgr._sources[("kraken", "BTC/USDT")] = "rest"
    summary = mgr.data_sources_summary()
    assert summary["binance"] == {"ws": 2, "rest": 1}
    assert summary["kraken"] == {"ws": 0, "rest": 1}


def test_data_source_default_unknown():
    """A pair with no observed update returns 'unknown'."""
    mgr = OrderBookManager()
    assert mgr.data_source("does", "NOT/EXIST") == "unknown"
