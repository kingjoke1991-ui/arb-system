"""Bounded fire-and-forget DB persistence for the dispatch hot path.

The cross-exchange scanner loop in ``app_bootstrap.py`` previously
``await``-ed ``opp_repo.save(...)`` synchronously for every detected
opportunity (rejected ones included). With ~2000 opportunities per scan
cycle, that queues 1-3 seconds of DB writes between scanner emit and
``HedgeCoordinator.execute()`` entry, blowing past
``max_signal_to_order_ms`` and aborting otherwise-valid hedges.

This module provides a tiny helper that offloads each persistence call
into a background task while applying bounded backpressure: at most
``max_inflight`` writes in flight at once. When the cap is reached,
``submit`` ``await``s for the next slot to free, so the dispatch loop
slows to match DB throughput rather than dropping data or stacking
unbounded tasks.

Failures inside a submitted coroutine are logged and never propagate to
the dispatch loop. Cancellation of the dispatch loop drains in-flight
writes via ``drain``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.common.logging import get_logger

log = get_logger("runtime.persistence")


class AsyncPersistence:
    """Bounded async persistence pool.

    Backpressure model: ``submit`` acquires a semaphore before launching
    the task. When the pool is at capacity, the dispatch loop ``await``s
    on ``submit`` itself (microseconds normally, longer under DB
    backlog), and that natural slowdown is what stops the in-flight task
    set from growing without bound. We do **not** drop writes on
    overflow — losing opportunity rows would silently destroy the
    audit trail the user relies on.
    """

    def __init__(self, max_inflight: int = 200, metrics: Any = None) -> None:
        self._max_inflight = max_inflight
        self._sem = asyncio.Semaphore(max_inflight)
        self._tasks: set[asyncio.Task] = set()
        self._metrics = metrics
        self._high_water = 0
        self._submitted_total = 0
        self._failed_total = 0

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    @property
    def high_water(self) -> int:
        return self._high_water

    @property
    def submitted_total(self) -> int:
        return self._submitted_total

    @property
    def failed_total(self) -> int:
        return self._failed_total

    async def submit(self, coro: Coroutine[Any, Any, Any], label: str = "persist") -> None:
        """Schedule ``coro`` to run in the background.

        Returns as soon as a slot is reserved (immediate when below cap).
        The caller MUST own ``coro`` exclusively — once submitted, the
        pool is responsible for awaiting and finalising it.
        """
        await self._sem.acquire()
        task = asyncio.create_task(self._run(coro, label), name=f"persist-{label}")
        self._tasks.add(task)
        self._submitted_total += 1
        n = len(self._tasks)
        if n > self._high_water:
            self._high_water = n
            self._update_metrics()
        else:
            self._update_metrics()
        task.add_done_callback(self._on_done)

    async def _run(self, coro: Coroutine[Any, Any, Any], label: str) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._failed_total += 1
            log.warning("persistence_error", label=label, error=str(e))
        finally:
            self._sem.release()

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        self._update_metrics()

    def _update_metrics(self) -> None:
        if self._metrics is None:
            return
        try:
            gauge = getattr(self._metrics, "persistence_inflight", None)
            if gauge is not None:
                gauge.set(len(self._tasks))
            hw = getattr(self._metrics, "persistence_inflight_high_water", None)
            if hw is not None:
                hw.set(self._high_water)
        except Exception:  # noqa: BLE001
            pass

    async def drain(self, timeout: float | None = 10.0) -> None:
        """Wait for all in-flight writes to complete (or timeout).

        Called during teardown so a clean shutdown doesn't drop opp /
        hedge rows that scanner queued in its last cycle. ``timeout`` is
        soft — if exceeded we log and move on; the alternative is
        hanging container shutdown indefinitely on a stuck DB.
        """
        if not self._tasks:
            return
        pending = list(self._tasks)
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Use the snapshot count, not ``len(self._tasks)``: when
            # ``wait_for`` raises here it has already cancelled the inner
            # ``gather``, which cancels all child tasks, and the
            # ``_on_done`` callbacks fire **before** this except block
            # runs — so ``self._tasks`` is typically empty by now.
            # Reporting 0 every time would defeat the purpose of the log.
            log.warning(
                "persistence_drain_timeout",
                pending=len(pending),
                timeout=timeout,
            )
