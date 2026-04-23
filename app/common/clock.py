"""Time utilities. Always use UTC. Inject Clock for tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def now_ms(self) -> int: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_ms(self) -> int:
        return int(self.now().timestamp() * 1000)


SYSTEM_CLOCK: Clock = SystemClock()


def utcnow() -> datetime:
    return SYSTEM_CLOCK.now()


def utcnow_ms() -> int:
    return SYSTEM_CLOCK.now_ms()
