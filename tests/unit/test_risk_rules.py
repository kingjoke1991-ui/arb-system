from datetime import datetime, timezone
from decimal import Decimal

from app.accounts.balance_manager import BalanceManager
from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.common.enums import Mode, RejectReason
from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.opportunity import ArbitrageOpportunity
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.exposure_manager import ExposureManager
from app.risk.health_guard import HealthGuard
from app.risk.kill_switch import KillSwitch
from app.risk.rules import RiskEngine


def _settings(mode: str = "dry-run") -> Settings:
    # Bypass .env during tests
    return Settings(
        mode=mode,
        enabled_symbols="BTC/USDT",
        min_net_edge_bps=Decimal("5"),
        min_profit_quote=Decimal("0.1"),
        min_order_size_quote=Decimal("10"),
        max_notional_per_trade=Decimal("100"),
        max_exposure_per_exchange=Decimal("1000"),
        max_total_open_hedges=5,
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )


def _book(exchange: str):
    return OrderBookSnapshot(
        exchange=exchange,
        symbol="BTC/USDT",
        bids=[OrderBookLevel(Decimal("99"), Decimal("10"))],
        asks=[OrderBookLevel(Decimal("100"), Decimal("10"))],
        ts_local=datetime.now(timezone.utc),
    )


def _opp(edge_bps: Decimal = Decimal("20"), size: Decimal = Decimal("1")) -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        opportunity_id="opp_1",
        symbol="BTC/USDT",
        buy_exchange="a",
        sell_exchange="b",
        buy_price=Decimal("100"),
        sell_price=Decimal("101"),
        gross_spread_bps=Decimal("100"),
        buy_fee_bps=Decimal("10"),
        sell_fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        buffer_bps=Decimal("0"),
        net_edge_bps=edge_bps,
        max_tradable_size=size,
        expected_profit_quote=size * Decimal("1"),
        detected_at=datetime.now(timezone.utc),
    )


def _engine(settings=None) -> RiskEngine:
    settings = settings or _settings()
    reg = AdapterRegistry([MockExchangeAdapter("a"), MockExchangeAdapter("b")])
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book("a"))
    books.update(_book("b"))
    bm = BalanceManager(reg)
    bm.set_virtual_balance("a", "USDT", Decimal("1000"))
    bm.set_virtual_balance("b", "BTC", Decimal("10"))
    health = HealthGuard(books, bm, settings)
    return RiskEngine(
        settings=settings,
        kill=KillSwitch(),
        breaker=CircuitBreaker(),
        health=health,
        exposure=ExposureManager(),
        balances=bm,
    )


def test_accept_simple_opp():
    eng = _engine()
    d = eng.evaluate(_opp())
    assert d.approved, d.reason
    assert d.approved_amount > 0


def test_reject_below_min_edge():
    eng = _engine()
    d = eng.evaluate(_opp(edge_bps=Decimal("1")))
    assert not d.approved
    assert d.reason == RejectReason.BELOW_MIN_EDGE


def test_reject_kill_switch():
    eng = _engine()
    eng._kill.turn_on("test")  # noqa: SLF001
    d = eng.evaluate(_opp())
    assert not d.approved
    assert d.reason == RejectReason.KILL_SWITCH


def test_reject_not_whitelisted():
    s = _settings()
    s.enabled_symbols = "ETH/USDT"
    eng = _engine(s)
    d = eng.evaluate(_opp())
    assert not d.approved
    assert d.reason == RejectReason.SYMBOL_NOT_WHITELISTED


def test_unrelated_symbol_staleness_does_not_block_trade():
    """Pre-fix the risk engine called the dashboard-level
    ``health.all_ok`` which checked **every** ``enabled_symbol`` for
    freshness on each leg. So a BTC/USDT opp on a→b would be
    EXCHANGE_UNHEALTHY-rejected as soon as ``enabled_symbols`` listed
    anything that 'a' or 'b' didn't actually have an orderbook for
    (this is what made kraken / coinbase / htx 'unhealthy' in
    production despite their BTC/USDT books being fresh).

    Post-fix: ``rules.py`` calls ``all_ok_for_trade`` which scopes the
    staleness check to the trade's actual symbol. An unrelated
    never-received symbol must NOT block a fresh BTC trade."""
    s = _settings()
    # Add an unrelated symbol to enabled list. No orderbook will ever
    # be populated for it on a/b, so dashboard ``all_ok`` would treat
    # both exchanges as unhealthy.
    s.enabled_symbols = "BTC/USDT,SHIB/USDT"
    eng = _engine(s)
    d = eng.evaluate(_opp())
    assert d.approved, f"Trade should be approved despite SHIB book missing; reason={d.reason}"


def test_traded_symbol_staleness_still_blocks_trade():
    """The per-symbol gate must still fire when the **opp's own
    symbol** is stale on either leg (so we don't accidentally weaken
    the protection while fixing the over-rejection)."""
    s = _settings()
    eng = _engine(s)
    # Use a max_stale_ms of 0 so the BTC books we just inserted
    # are immediately considered stale. We rebuild the book manager.
    eng._health._books = OrderBookManager(max_stale_ms=0)  # noqa: SLF001
    eng._health._books.update(_book("a"))  # noqa: SLF001
    eng._health._books.update(_book("b"))  # noqa: SLF001
    # Sleep beyond max_stale_ms via update timestamp manipulation:
    # the freshly-inserted books with max_stale_ms=0 are stale on the
    # next clock read.
    import time

    time.sleep(0.001)
    d = eng.evaluate(_opp())
    assert not d.approved
    assert d.reason == RejectReason.EXCHANGE_UNHEALTHY
