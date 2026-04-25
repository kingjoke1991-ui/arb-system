"""Issue 2 — circuit breaker tracks consecutive losing trades, not just
order-leg failures."""

from decimal import Decimal

from app.risk.circuit_breaker import CircuitBreaker


def test_consecutive_losses_trip_with_threshold_set():
    cb = CircuitBreaker(max_consecutive_failures=999, max_consecutive_losing_trades=3)
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-2"))
    assert not cb.tripped
    cb.record_pnl(Decimal("-0.5"))
    assert cb.tripped
    assert "consecutive losing" in (cb.reason or "")


def test_profitable_trade_resets_consecutive_loss_count():
    cb = CircuitBreaker(max_consecutive_failures=999, max_consecutive_losing_trades=3)
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-2"))
    cb.record_pnl(Decimal("0.5"))  # winner — resets
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-1"))
    assert not cb.tripped


def test_threshold_none_disables_pnl_tripping():
    cb = CircuitBreaker(max_consecutive_failures=999, max_consecutive_losing_trades=None)
    for _ in range(50):
        cb.record_pnl(Decimal("-100"))
    assert not cb.tripped


def test_none_pnl_is_ignored():
    cb = CircuitBreaker(max_consecutive_failures=999, max_consecutive_losing_trades=2)
    cb.record_pnl(None)
    cb.record_pnl(None)
    assert not cb.tripped
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-1"))
    assert cb.tripped


def test_zero_pnl_resets_loss_count():
    cb = CircuitBreaker(max_consecutive_failures=999, max_consecutive_losing_trades=2)
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("0"))
    cb.record_pnl(Decimal("-1"))
    assert not cb.tripped
