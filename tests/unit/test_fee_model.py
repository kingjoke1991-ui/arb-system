from decimal import Decimal

from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.strategy.fee_model import FeeModel, FeeTable


def test_fee_model_defaults_to_adapter_rate():
    a = MockExchangeAdapter("a", fee_bps=Decimal("15"))
    reg = AdapterRegistry([a])
    model = FeeModel(reg)
    # adapter returns 15bps -> convert to factor 0.0015 -> back to 15bps
    assert model.taker_bps("a", "BTC/USDT", "buy") == Decimal("15")


def test_fee_model_cost():
    a = MockExchangeAdapter("a", fee_bps=Decimal("10"))
    reg = AdapterRegistry([a])
    model = FeeModel(reg)
    cost = model.fee_cost_quote("a", "BTC/USDT", "buy", Decimal("1000"))
    assert cost == Decimal("1")  # 10bps of 1000 = 1
