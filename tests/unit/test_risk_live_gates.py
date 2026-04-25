"""Issue 2 — live-mode hard gates: daily loss + consecutive losses + book age."""

from datetime import datetime, timezone
from decimal import Decimal

from app.accounts.balance_manager import BalanceManager
from app.adapters.mock_adapter import MockExchangeAdapter
from app.adapters.registry import AdapterRegistry
from app.common.enums import RejectReason
from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.opportunity import ArbitrageOpportunity
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.exposure_manager import ExposureManager
from app.risk.health_guard import HealthGuard
from app.risk.kill_switch import KillSwitch
from app.risk.rules import RiskEngine


def _settings(mode: str = "live", **kw) -> Settings:
    base = dict(
        mode=mode,
        enabled_symbols="BTC/USDT",
        min_net_edge_bps=Decimal("5"),
        min_profit_quote=Decimal("0.1"),
        min_order_size_quote=Decimal("10"),
        max_notional_per_trade=Decimal("100"),
        max_exposure_per_exchange=Decimal("1000"),
        max_total_open_hedges=5,
        max_book_age_ms_for_trade=600,
        max_daily_loss_quote=Decimal("50"),
        max_consecutive_losing_trades=3,
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )
    base.update(kw)
    return Settings(**base)


def _book(exchange: str):
    return OrderBookSnapshot(
        exchange=exchange,
        symbol="BTC/USDT",
        bids=[OrderBookLevel(Decimal("99"), Decimal("10"))],
        asks=[OrderBookLevel(Decimal("100"), Decimal("10"))],
        ts_local=datetime.now(timezone.utc),
    )


def _opp(buy_age=50, sell_age=50, edge_bps=Decimal("20")) -> ArbitrageOpportunity:
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
        max_tradable_size=Decimal("1"),
        expected_profit_quote=Decimal("1"),
        detected_at=datetime.now(timezone.utc),
        buy_book_age_ms=buy_age,
        sell_book_age_ms=sell_age,
    )


def _engine(settings: Settings, breaker: CircuitBreaker | None = None) -> RiskEngine:
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
        breaker=breaker
        or CircuitBreaker(max_consecutive_losing_trades=settings.max_consecutive_losing_trades),
        health=health,
        exposure=ExposureManager(),
        balances=bm,
    )


def test_book_too_old_rejected_even_when_feed_is_live():
    eng = _engine(_settings(mode="paper-trade"))
    d = eng.evaluate(_opp(buy_age=800, sell_age=50))
    assert not d.approved
    assert d.reason == RejectReason.BOOK_TOO_OLD


def test_fresh_book_passes_age_gate():
    eng = _engine(_settings(mode="paper-trade"))
    d = eng.evaluate(_opp(buy_age=200, sell_age=200))
    assert d.approved


def test_live_daily_loss_limit_rejects():
    eng = _engine(_settings(mode="live"))
    eng.record_realized_pnl(Decimal("-30"))
    eng.record_realized_pnl(Decimal("-25"))
    d = eng.evaluate(_opp())
    assert not d.approved
    assert d.reason == RejectReason.DAILY_LOSS_LIMIT


def test_live_consecutive_loss_limit_rejects():
    breaker = CircuitBreaker(max_consecutive_losing_trades=3)
    breaker.record_pnl(Decimal("-1"))
    breaker.record_pnl(Decimal("-1"))
    breaker.record_pnl(Decimal("-1"))
    eng = _engine(_settings(mode="live"), breaker=breaker)
    d = eng.evaluate(_opp())
    assert not d.approved
    # Either the breaker tripped path or the explicit consecutive-loss
    # path reject this (both are correct outcomes).
    assert d.reason in (RejectReason.CONSECUTIVE_LOSS_LIMIT, RejectReason.CIRCUIT_BREAKER)


def test_paper_trade_mode_does_not_apply_pnl_gates():
    """Paper-trade should not be gated by the daily-loss limit so paper
    backtests can keep running even when accumulated paper-PnL is bad."""
    eng = _engine(_settings(mode="paper-trade"))
    eng.record_realized_pnl(Decimal("-100"))
    d = eng.evaluate(_opp())
    assert d.approved
