"""``AsyncPersistence`` unit tests.

Background: the cross-exchange dispatch loop in ``app_bootstrap.py``
previously blocked on ``await opp_repo.save(...)`` per opportunity,
adding 1-3s of cumulative latency before ``HedgeCoordinator.execute()``
ran. ``AsyncPersistence`` offloads those writes; these tests pin the
non-blocking, bounded-backpressure, and failure-isolation guarantees
the dispatch loop relies on.
"""

from __future__ import annotations

import asyncio

import pytest

from app.runtime.persistence import AsyncPersistence


@pytest.mark.asyncio
async def test_submit_returns_immediately_when_below_cap() -> None:
    pool = AsyncPersistence(max_inflight=10)

    # A coroutine that takes 200ms — if submit() waits for it to finish,
    # the assert below will be visibly slow.
    async def slow_coro() -> None:
        await asyncio.sleep(0.2)

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    for _ in range(5):
        await pool.submit(slow_coro())
    elapsed = loop.time() - t0

    # 5 submits, each scheduling a 200ms task — the submits themselves
    # must finish in well under one task duration. 50ms is slack for CI.
    assert elapsed < 0.05, f"submit() blocked the dispatch loop ({elapsed * 1000:.1f}ms)"
    assert pool.in_flight == 5
    await pool.drain()
    assert pool.in_flight == 0


@pytest.mark.asyncio
async def test_submit_blocks_at_cap_until_a_slot_frees() -> None:
    pool = AsyncPersistence(max_inflight=2)
    gate = asyncio.Event()

    async def held() -> None:
        await gate.wait()

    # Saturate the pool.
    await pool.submit(held())
    await pool.submit(held())
    assert pool.in_flight == 2

    # Third submit must wait until a slot frees.
    submitted = asyncio.Event()

    async def submit_third() -> None:
        await pool.submit(held())
        submitted.set()

    third_task = asyncio.create_task(submit_third())
    # Give the event loop a tick to let submit_third() reach the
    # acquire() call.
    await asyncio.sleep(0.02)
    assert not submitted.is_set(), "submit() should still be blocked at cap"

    # Release the gate so all three coros can finish.
    gate.set()
    await asyncio.wait_for(third_task, timeout=1.0)
    assert submitted.is_set()
    await pool.drain()


@pytest.mark.asyncio
async def test_submit_does_not_propagate_coro_failures() -> None:
    pool = AsyncPersistence(max_inflight=10)

    async def boom() -> None:
        raise RuntimeError("db unavailable")

    # submit() must return cleanly even though the coro raises.
    await pool.submit(boom(), label="boom")
    await pool.drain()

    assert pool.failed_total == 1

    # And the slot must be released so subsequent submits don't deadlock.
    async def ok() -> None:
        return None

    for _ in range(20):
        await pool.submit(ok())
    await pool.drain()
    assert pool.in_flight == 0


@pytest.mark.asyncio
async def test_high_water_tracks_peak_concurrency() -> None:
    pool = AsyncPersistence(max_inflight=50)
    gate = asyncio.Event()

    async def held() -> None:
        await gate.wait()

    for _ in range(7):
        await pool.submit(held())
    assert pool.high_water == 7

    gate.set()
    await pool.drain()
    # Watermark is sticky — it doesn't ratchet down after drain.
    assert pool.high_water == 7

    # New burst lower than the previous peak doesn't reduce it.
    gate2 = asyncio.Event()

    async def held2() -> None:
        await gate2.wait()

    for _ in range(3):
        await pool.submit(held2())
    assert pool.high_water == 7
    gate2.set()
    await pool.drain()


@pytest.mark.asyncio
async def test_drain_returns_when_no_tasks_pending() -> None:
    pool = AsyncPersistence(max_inflight=10)
    # No-op drain on empty pool must return immediately.
    await asyncio.wait_for(pool.drain(), timeout=0.1)


@pytest.mark.asyncio
async def test_drain_timeout_does_not_raise_and_cancels_stuck_tasks() -> None:
    """A stuck DB write must not block container shutdown indefinitely.
    When ``drain(timeout=…)`` exceeds its budget we let asyncio cancel
    the inner tasks (via ``wait_for`` cancelling its inner gather) and
    log a warning rather than re-raising. The next teardown step (close
    DB) is then free to proceed."""
    pool = AsyncPersistence(max_inflight=10)
    gate = asyncio.Event()

    async def stuck() -> None:
        await gate.wait()

    await pool.submit(stuck())
    assert pool.in_flight == 1
    # Drain with a short timeout — this must NOT raise.
    await pool.drain(timeout=0.05)
    # The stuck task should have been cancelled by the timeout, and
    # ``_run``'s finally clause should have released its slot. A fresh
    # submit should immediately succeed.
    assert pool.in_flight == 0

    # Set the gate (no-op now since the task was cancelled) and submit
    # a fresh write to confirm the pool isn't deadlocked.
    gate.set()

    async def ok() -> None:
        return None

    await pool.submit(ok())
    await pool.drain(timeout=1.0)
    assert pool.in_flight == 0


@pytest.mark.asyncio
async def test_metrics_gauges_are_updated_when_provided() -> None:
    """If a metrics object with the right gauges is passed, they are
    kept in sync with in-flight count. Bootstrap relies on this for the
    /metrics dashboard panel."""

    class _Gauge:
        def __init__(self) -> None:
            self.value: float = 0.0

        def set(self, v: float) -> None:
            self.value = float(v)

    class _Metrics:
        def __init__(self) -> None:
            self.persistence_inflight = _Gauge()
            self.persistence_inflight_high_water = _Gauge()

    metrics = _Metrics()
    pool = AsyncPersistence(max_inflight=10, metrics=metrics)
    gate = asyncio.Event()

    async def held() -> None:
        await gate.wait()

    for _ in range(4):
        await pool.submit(held())
    assert metrics.persistence_inflight.value == 4
    assert metrics.persistence_inflight_high_water.value == 4

    gate.set()
    await pool.drain()
    assert metrics.persistence_inflight.value == 0
    # High water remains.
    assert metrics.persistence_inflight_high_water.value == 4
