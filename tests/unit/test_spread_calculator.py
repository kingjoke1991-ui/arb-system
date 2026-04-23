from datetime import datetime, timezone
from decimal import Decimal

from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.strategy.fee_model import FeeModel, FeeTable
from app.strategy.spread_calculator import SpreadCalculator


def _book(exchange, bids, asks):
    return OrderBookSnapshot(
        exchange=exchange,
        symbol="BTC/USDT",
        bids=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in bids],
        asks=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in asks],
        ts_local=datetime.now(timezone.utc),
    )


def test_spread_calc_positive_edge():
    mock_a = MockExchangeAdapter(name="a")
    mock_b = MockExchangeAdapter(name="b")
    reg = AdapterRegistry([mock_a, mock_b])
    # override both adapters' fee to 0 for clean arithmetic
    table = FeeTable(default_bps=Decimal("0"))
    mock_a._fee_bps = Decimal("0")  # noqa: SLF001
    mock_b._fee_bps = Decimal("0")  # noqa: SLF001
    fees = FeeModel(reg, table=table)

    calc = SpreadCalculator(fees, buffer_bps=Decimal("0"))
    buy = _book("a", bids=[(99, 1)], asks=[(100, 1)])
    sell = _book("b", bids=[(101, 1)], asks=[(102, 1)])
    est = calc.evaluate_direction("BTC/USDT", buy, sell, Decimal("0.1"))
    assert est is not None
    assert est.max_tradable_base == Decimal("0.1")
    # edge = (101 - 100) / 100 = 100bps
    assert est.net_edge_bps == Decimal("100")


def test_spread_calc_fees_eat_edge():
    mock_a = MockExchangeAdapter(name="a", fee_bps=Decimal("60"))
    mock_b = MockExchangeAdapter(name="b", fee_bps=Decimal("60"))
    reg = AdapterRegistry([mock_a, mock_b])
    fees = FeeModel(reg)
    calc = SpreadCalculator(fees, buffer_bps=Decimal("0"))
    buy = _book("a", bids=[(99, 1)], asks=[(100, 1)])
    sell = _book("b", bids=[(101, 1)], asks=[(102, 1)])
    est = calc.evaluate_direction("BTC/USDT", buy, sell, Decimal("0.1"))
    assert est is not None
    # gross ~100bps, two-sided 60bps taker fees ~ 120bps → net negative
    assert est.net_edge_bps < 0
