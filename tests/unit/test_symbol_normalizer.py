from app.marketdata.symbol_normalizer import SymbolNormalizer
from app.models.symbol import parse_canonical


def test_parse_canonical():
    s = parse_canonical("btc/usdt")
    assert s.base == "BTC"
    assert s.quote == "USDT"
    assert s.canonical == "BTC/USDT"


def test_normalizer_default_passthrough():
    n = SymbolNormalizer()
    assert n.to_venue("binance", "BTC/USDT") == "BTC/USDT"


def test_normalizer_override():
    n = SymbolNormalizer()
    n.set_override("binance", "BTC/USDT", "BTCUSDT")
    assert n.to_venue("binance", "BTC/USDT") == "BTCUSDT"
