"""
Circuit breaker tracks recent failures. Trips on N consecutive failures,
or failure ratio over a rolling window.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


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
    ):
        self._max_consec = max_consecutive_failures
        self._window_sec = window_sec
        self._window_min = window_min_samples
        self._window_ratio = window_failure_ratio
        self._samples: deque[_Sample] = deque()
        self._consecutive_failures = 0
        self._tripped = False
        self._trip_reason: str | None = None

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def reason(self) -> str | None:
        return self._trip_reason

    def reset(self) -> None:
        self._samples.clear()
        self._consecutive_failures = 0
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

    def status(self) -> dict:
        return {
            "tripped": self._tripped,
            "reason": self._trip_reason,
            "consecutive_failures": self._consecutive_failures,
            "window_samples": len(self._samples),
        }
