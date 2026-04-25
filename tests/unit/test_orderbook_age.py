from datetime import datetime, timezone
from decimal import Decimal

from app.marketdata.orderbook_manager import OrderBookManager
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot


def _book(exchange: str = "x") -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange=exchange,
        symbol="BTC/USDT",
        bids=[OrderBookLevel(Decimal("99"), Decimal("1"))],
        asks=[OrderBookLevel(Decimal("100"), Decimal("1"))],
        ts_local=datetime.now(timezone.utc),
    )


def test_age_ms_zero_for_just_received():
    m = OrderBookManager()
    m.update(_book("x"))
    age = m.age_ms("x", "BTC/USDT")
    assert age is not None
    assert age >= 0
    assert age < 100


def test_age_ms_none_when_unknown():
    m = OrderBookManager()
    assert m.age_ms("x", "BTC/USDT") is None


def test_age_ms_grows_against_now_ms():
    m = OrderBookManager()
    m.update(_book("x"))
    # Push the wall-clock forward via the optional now_ms argument.
    age = m.age_ms("x", "BTC/USDT", now_ms=int(datetime.now(timezone.utc).timestamp() * 1000) + 5000)
    assert age is not None
    assert age >= 4000
