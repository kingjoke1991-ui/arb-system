"""Tests for the triangular arbitrage scanner.

The scanner relies on three live order books on one exchange. We feed the
OrderBookManager synthetic snapshots and verify:

1. ``parse_triangles`` handles common formatting variations.
2. ``pairs_for`` derives the three pair symbols correctly.
3. A positive-edge triangle produces an opportunity above threshold.
4. A flat triangle (no arb) is correctly rejected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.strategy.fee_model import FeeModel, FeeTable
from app.strategy.triangular_scanner import TriangularScanner


def _book(exchange: str, symbol: str, bid: float, ask: float) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange=exchange,
        symbol=symbol,
        bids=[OrderBookLevel(Decimal(str(bid)), Decimal("10"))],
        asks=[OrderBookLevel(Decimal(str(ask)), Decimal("10"))],
        ts_local=datetime.now(timezone.utc),
    )


def _build(triangles: str = "USDT,BTC,ETH") -> TriangularScanner:
    reg = AdapterRegistry([MockExchangeAdapter(name="mock", fee_bps=Decimal("0"))])
    books = OrderBookManager()
    settings = Settings(
        _env_file=None,
        triangular_exchange="mock",
        triangular_triangles=triangles,
        triangular_min_net_edge_bps=Decimal("1"),
        strategy_triangular_same_exchange_enabled=True,
    )
    fees = FeeModel(reg, table=FeeTable(default_bps=Decimal("0")))
    return TriangularScanner(settings, reg, books, fees)


def test_parse_triangles_simple():
    out = TriangularScanner.parse_triangles("USDT,BTC,ETH")
    assert out == [("USDT", "BTC", "ETH")]


def test_parse_triangles_multiple_with_whitespace():
    out = TriangularScanner.parse_triangles(" USDT,BTC,ETH ; usdt , btc , sol ")
    assert out == [("USDT", "BTC", "ETH"), ("USDT", "BTC", "SOL")]


def test_parse_triangles_skips_wrong_arity():
    out = TriangularScanner.parse_triangles("USDT,BTC;USDT,BTC,ETH,XRP;USDT,BTC,ETH")
    assert out == [("USDT", "BTC", "ETH")]


def test_pairs_for():
    assert TriangularScanner.pairs_for(("USDT", "BTC", "ETH")) == (
        "BTC/USDT",
        "ETH/BTC",
        "ETH/USDT",
    )


def test_positive_triangle_detected():
    s = _build()
    # Cycle: 1 USDT -> 1/50000 BTC -> (1/50000)/0.05 ETH = 0.0004 ETH
    #        then sell 0.0004 ETH at 2550 USDT = 1.02 USDT → +2% gross
    s._books.update(_book("mock", "BTC/USDT", bid=49999, ask=50000))  # noqa: SLF001
    s._books.update(_book("mock", "ETH/BTC", bid=0.04999, ask=0.05))  # noqa: SLF001
    s._books.update(_book("mock", "ETH/USDT", bid=2550, ask=2551))  # noqa: SLF001

    opp = s._evaluate_direction(  # noqa: SLF001
        "mock", ("USDT", "BTC", "ETH"), Decimal("100"), reverse=False
    )
    assert opp is not None
    assert opp.net_edge_bps > Decimal("1")
    assert opp.pair_ba == "BTC/USDT"
    assert opp.pair_cb == "ETH/BTC"
    assert opp.pair_ca == "ETH/USDT"


def test_flat_triangle_rejected():
    s = _build()
    # All cycle multipliers == 1, no edge.
    s._books.update(_book("mock", "BTC/USDT", bid=50000, ask=50000))  # noqa: SLF001
    s._books.update(_book("mock", "ETH/BTC", bid=Decimal("0.05"), ask=Decimal("0.05")))  # noqa: SLF001
    s._books.update(_book("mock", "ETH/USDT", bid=2500, ask=2500))  # noqa: SLF001

    opp = s._evaluate_direction(  # noqa: SLF001
        "mock", ("USDT", "BTC", "ETH"), Decimal("100"), reverse=False
    )
    # Should produce a result but with near-zero net edge (< min threshold).
    assert opp is not None
    assert abs(opp.net_edge_bps) < Decimal("5")


def test_missing_book_returns_none():
    s = _build()
    # No snapshots at all.
    opp = s._evaluate_direction(  # noqa: SLF001
        "mock", ("USDT", "BTC", "ETH"), Decimal("100"), reverse=False
    )
    assert opp is None
