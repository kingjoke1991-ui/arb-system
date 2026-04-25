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


def test_threshold_getter_picks_up_runtime_changes():
    """``max_consecutive_losing_trades_getter`` is consulted on every
    ``record_pnl`` call so runtime config edits apply without rebuilding
    the breaker. This is the contract relied on by ConfigService when an
    operator raises the threshold from the /config UI panel."""
    threshold = {"value": 3}
    cb = CircuitBreaker(
        max_consecutive_failures=999,
        max_consecutive_losing_trades_getter=lambda: threshold["value"],
    )
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-1"))
    assert not cb.tripped
    threshold["value"] = 10  # operator raises threshold via UI
    for _ in range(7):
        cb.record_pnl(Decimal("-1"))
    # Total losses now 9, threshold 10 → still not tripped.
    assert not cb.tripped
    cb.record_pnl(Decimal("-1"))  # 10th loss
    assert cb.tripped


def test_getter_returning_none_falls_back_to_static_threshold():
    """Getter returning ``None`` (e.g. attribute missing on legacy
    Settings) must fall back to the static value passed at construction
    rather than disabling the breaker silently."""
    cb = CircuitBreaker(
        max_consecutive_failures=999,
        max_consecutive_losing_trades=2,
        max_consecutive_losing_trades_getter=lambda: None,
    )
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-1"))
    assert cb.tripped


def test_getter_overrides_static_value():
    """When both forms are provided and the getter returns a non-None
    value, the getter wins. Tests the precedence rule documented on
    ``__init__``."""
    cb = CircuitBreaker(
        max_consecutive_failures=999,
        max_consecutive_losing_trades=2,
        max_consecutive_losing_trades_getter=lambda: 5,
    )
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-1"))
    assert not cb.tripped  # static was 2, but getter says 5
    cb.record_pnl(Decimal("-1"))
    assert cb.tripped


def test_getter_exception_falls_back_to_static_threshold():
    """A flaky getter must not crash record_pnl; it falls back to the
    static value (``None`` here) like the getter returned ``None``."""

    def boom() -> int | None:
        raise RuntimeError("settings unavailable")

    cb = CircuitBreaker(
        max_consecutive_failures=999,
        max_consecutive_losing_trades=2,
        max_consecutive_losing_trades_getter=boom,
    )
    cb.record_pnl(Decimal("-1"))
    cb.record_pnl(Decimal("-1"))
    assert cb.tripped
