"""
Circuit breaker tracks recent failures. Trips on N consecutive failures,
or failure ratio over a rolling window.

Also tracks realized PnL outcomes so a string of profitable-on-paper but
actually-losing trades trips the breaker even when every order leg
"fills" successfully (which the failure counter alone misses).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class _Sample:
    ts: float
    ok: bool


class CircuitBreaker:
    def __init__(
        self,
        max_consecutive_failures: int = 5,
        window_sec: int = 60,
        window_min_samples: int = 10,
        window_failure_ratio: float = 0.5,
        max_consecutive_losing_trades: int | None = None,
        max_consecutive_losing_trades_getter: Callable[[], int | None] | None = None,
    ):
        self._max_consec = max_consecutive_failures
        self._window_sec = window_sec
        self._window_min = window_min_samples
        self._window_ratio = window_failure_ratio
        self._samples: deque[_Sample] = deque()
        self._consecutive_failures = 0
        self._tripped = False
        self._trip_reason: str | None = None
        # PnL-side tracking. ``None`` disables consecutive-loss tripping
        # so legacy callers (and unit tests) keep their behaviour.
        # The getter form takes precedence so that runtime edits to
        # ``Settings.max_consecutive_losing_trades`` (via ConfigService)
        # are picked up on the next ``record_pnl`` call without having
        # to rebuild the breaker.
        self._max_consecutive_losses_static = max_consecutive_losing_trades
        self._max_consecutive_losses_getter = max_consecutive_losing_trades_getter
        self._consecutive_losses = 0

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def reason(self) -> str | None:
        return self._trip_reason

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    def reset(self) -> None:
        self._samples.clear()
        self._consecutive_failures = 0
        self._consecutive_losses = 0
        self._tripped = False
        self._trip_reason = None

    def record(self, ok: bool) -> None:
        now = time.time()
        self._samples.append(_Sample(now, ok))
        while self._samples and (now - self._samples[0].ts) > self._window_sec:
            self._samples.popleft()

        if ok:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1

        if self._consecutive_failures >= self._max_consec:
            self._tripped = True
            self._trip_reason = f"{self._consecutive_failures} consecutive failures"
            return

        if len(self._samples) >= self._window_min:
            failures = sum(1 for s in self._samples if not s.ok)
            if failures / len(self._samples) >= self._window_ratio:
                self._tripped = True
                self._trip_reason = f"{failures}/{len(self._samples)} failures in {self._window_sec}s"

    def _max_consecutive_losses(self) -> int | None:
        """Resolve the consecutive-loss threshold at call-time.

        Getter is consulted first so runtime config edits take effect
        immediately; falls back to the static value passed at
        construction so existing callers keep working unchanged.
        """
        if self._max_consecutive_losses_getter is not None:
            try:
                value = self._max_consecutive_losses_getter()
            except Exception:  # noqa: BLE001
                value = None
            if value is not None:
                return int(value)
        return self._max_consecutive_losses_static

    def record_pnl(self, realized_pnl_quote: Decimal | None) -> None:
        """Feed the realized PnL of a finished hedge group into the breaker.

        ``None`` is ignored (e.g. dry-run, or pre-issue-1 missing data); a
        non-positive value increments the consecutive-loss counter, a
        positive value resets it.
        """
        if realized_pnl_quote is None:
            return
        if realized_pnl_quote >= 0:
            self._consecutive_losses = 0
            return
        self._consecutive_losses += 1
        threshold = self._max_consecutive_losses()
        if threshold is not None and self._consecutive_losses >= threshold:
            self._tripped = True
            self._trip_reason = f"{self._consecutive_losses} consecutive losing trades"

    def status(self) -> dict:
        return {
            "tripped": self._tripped,
            "reason": self._trip_reason,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_losses": self._consecutive_losses,
            "window_samples": len(self._samples),
        }
