from datetime import datetime, timezone
from decimal import Decimal

from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.strategy.slippage_model import max_fillable_buy, max_fillable_sell, vwap_buy, vwap_sell


def _book():
    return OrderBookSnapshot(
        exchange="x",
        symbol="BTC/USDT",
        bids=[OrderBookLevel(Decimal("99"), Decimal("1")), OrderBookLevel(Decimal("98"), Decimal("2"))],
        asks=[OrderBookLevel(Decimal("100"), Decimal("1")), OrderBookLevel(Decimal("101"), Decimal("2"))],
        ts_local=datetime.now(timezone.utc),
    )


def test_vwap_buy_shallow():
    vwap, filled, slip = vwap_buy(_book(), Decimal("0.5"))
    assert vwap == Decimal("100")
    assert filled == Decimal("0.5")
    assert slip == Decimal("0")


def test_vwap_buy_walks_levels():
    vwap, filled, slip = vwap_buy(_book(), Decimal("2"))
    # 1 @ 100 + 1 @ 101 = 201/2 = 100.5
    assert filled == Decimal("2")
    assert vwap == Decimal("100.5")
    assert slip == Decimal("50")  # 0.5/100 = 50bps


def test_vwap_sell_walks_levels():
    vwap, filled, slip = vwap_sell(_book(), Decimal("2"))
    # 1 @ 99 + 1 @ 98 = 197/2 = 98.5
    assert filled == Decimal("2")
    assert vwap == Decimal("98.5")
    assert slip > 0


def test_max_fillable():
    b = _book()
    assert max_fillable_buy(b) == Decimal("3")
    assert max_fillable_sell(b) == Decimal("3")
