from app.risk.circuit_breaker import CircuitBreaker


def test_consecutive_failures_trip():
    cb = CircuitBreaker(max_consecutive_failures=3, window_min_samples=999)
    for _ in range(2):
        cb.record(False)
    assert not cb.tripped
    cb.record(False)
    assert cb.tripped


def test_success_resets_consecutive():
    cb = CircuitBreaker(max_consecutive_failures=3, window_min_samples=999)
    cb.record(False)
    cb.record(False)
    cb.record(True)
    cb.record(False)
    assert not cb.tripped
