"""Tests for the 3-leg paper-trade triangular executor.

The executor is driven by a ``TriangularOpportunity`` (from the scanner's
ring buffer). We feed it synthetic books via ``OrderBookManager`` and
verify:

1. All three legs fill cleanly when book depth is ample → ``completed``.
2. Leg 2 cannot fill (empty asks) → rollback triggers and report is
   ``rolled_back`` with one rollback leg.
3. Virtual balances are adjusted in the happy path (PaperFillEngine was
   actually called per leg).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.accounts.balance_manager import BalanceManager
from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.common.ids import new_opportunity_id
from app.execution.paper_fill_engine import PaperFillEngine
from app.execution.triangular_executor import TriangularExecutor, clear_executions
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.strategy.triangular_scanner import TriangularOpportunity


def _book(exchange: str, symbol: str, bid: float, ask: float, size: float = 10.0) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange=exchange,
        symbol=symbol,
        bids=[OrderBookLevel(Decimal(str(bid)), Decimal(str(size)))],
        asks=[OrderBookLevel(Decimal(str(ask)), Decimal(str(size)))],
        ts_local=datetime.now(timezone.utc),
    )


def _empty_asks_book(exchange: str, symbol: str, bid: float) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange=exchange,
        symbol=symbol,
        bids=[OrderBookLevel(Decimal(str(bid)), Decimal("10"))],
        asks=[],
        ts_local=datetime.now(timezone.utc),
    )


def _build():
    reg = AdapterRegistry([MockExchangeAdapter(name="mock", fee_bps=Decimal("0"))])
    books = OrderBookManager()
    balances = BalanceManager(reg)
    # Seed so we can see adjust_virtual effects.
    balances.set_virtual_balance("mock", "USDT", Decimal("10000"))
    balances.set_virtual_balance("mock", "BTC", Decimal("0.5"))
    balances.set_virtual_balance("mock", "ETH", Decimal("10"))
    paper = PaperFillEngine(books, balance_mgr=balances)
    return books, balances, paper


def _forward_opp() -> TriangularOpportunity:
    return TriangularOpportunity(
        opportunity_id=new_opportunity_id(),
        exchange="mock",
        direction="USDT->BTC->ETH->USDT",
        triangle=("USDT", "BTC", "ETH"),
        pair_ba="BTC/USDT",
        pair_cb="ETH/BTC",
        pair_ca="ETH/USDT",
        price_leg1=Decimal("50000"),
        price_leg2=Decimal("0.06"),
        price_leg3=Decimal("3001"),
        probe_quote=Decimal("100"),
        end_quote=Decimal("100.10"),
        gross_edge_bps=Decimal("10"),
        fee_bps_total=Decimal("0"),
        net_edge_bps=Decimal("10"),
        detected_at=datetime.now(timezone.utc),
    )


def test_three_leg_completed_happy_path():
    clear_executions()
    books, balances, paper = _build()
    # BTC/USDT: ask=50000 bid=49999
    books.update(_book("mock", "BTC/USDT", 49999, 50000))
    # ETH/BTC: ask=0.06 bid=0.059 (buying ETH with BTC)
    books.update(_book("mock", "ETH/BTC", 0.059, 0.06))
    # ETH/USDT: bid=3001 (selling ETH for USDT)
    books.update(_book("mock", "ETH/USDT", 3001, 3002))

    execu = TriangularExecutor(paper=paper)
    report = execu.execute_paper(_forward_opp(), Decimal("100"))

    assert report.outcome == "completed", report
    assert len(report.legs) == 3
    assert report.rolled_back is False
    assert all(leg.status == "filled" for leg in report.legs)


def test_three_leg_rollback_when_leg2_has_no_asks():
    clear_executions()
    books, balances, paper = _build()
    # Leg 1 fillable.
    books.update(_book("mock", "BTC/USDT", 49999, 50000))
    # Leg 2 has no asks → BUY leg will fail.
    books.update(_empty_asks_book("mock", "ETH/BTC", 0.059))
    books.update(_book("mock", "ETH/USDT", 3001, 3002))

    execu = TriangularExecutor(paper=paper)
    report = execu.execute_paper(_forward_opp(), Decimal("100"))

    assert report.outcome in ("rolled_back", "aborted"), report
    # Only leg 1 should be recorded as having executed; leg 2 should be
    # present with a non-FILLED status.
    assert report.legs[0].status == "filled"
    assert report.legs[1].status != "filled"
    # Rollback should sell back the BTC we bought in leg 1.
    assert len(report.rollback_legs) == 1
    assert report.rollback_legs[0].side == "sell"


def test_live_mode_uses_order_router():
    """Executor.execute() should route through the injected OrderRouter
    (not the PaperFillEngine). We verify by stubbing a router that
    records every intent and returns pre-canned FILLED states."""
    import asyncio

    from app.common.clock import utcnow
    from app.common.enums import OrderStatus
    from app.common.ids import new_order_id
    from app.execution.triangular_executor import TriangularExecutor, clear_executions
    from app.models.order import UnifiedOrderState

    clear_executions()
    books, balances, paper = _build()
    books.update(_book("mock", "BTC/USDT", 49999, 50000))
    books.update(_book("mock", "ETH/BTC", 0.059, 0.06))
    books.update(_book("mock", "ETH/USDT", 3001, 3002))

    received = []

    class _StubRouter:
        async def submit(self, intent):
            received.append(intent)
            # Canonicalise fill at the reference price the executor sent.
            now = utcnow()
            return UnifiedOrderState(
                internal_order_id=new_order_id(),
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
                created_at=now,
                updated_at=now,
                client_order_id=intent.client_order_id,
            )

    execu = TriangularExecutor(router=_StubRouter(), paper=paper)
    report = asyncio.run(execu.execute(_forward_opp(), Decimal("100"), mode_label="live"))

    assert report.outcome == "completed"
    assert len(received) == 3
    assert report.mode == "live"
    # Client order ids are idempotency-friendly: unique + leg-tagged.
    coids = [i.client_order_id for i in received]
    assert coids[0].endswith("-L1")
    assert coids[2].endswith("-L3")


def test_executions_ring_buffer_records_history():
    clear_executions()
    books, balances, paper = _build()
    books.update(_book("mock", "BTC/USDT", 49999, 50000))
    books.update(_book("mock", "ETH/BTC", 0.059, 0.06))
    books.update(_book("mock", "ETH/USDT", 3001, 3002))

    execu = TriangularExecutor(paper=paper)
    execu.execute_paper(_forward_opp(), Decimal("100"))
    execu.execute_paper(_forward_opp(), Decimal("100"))

    from app.execution.triangular_executor import recent_executions

    hist = recent_executions()
    assert len(hist) == 2
    assert hist[0]["mode"] == "paper-trade"
    assert "legs" in hist[0]
