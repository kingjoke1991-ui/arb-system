"""
Kill switch — simplest possible global on/off. Backed by in-memory + (optional) Redis.
"""

from __future__ import annotations

from app.common.clock import utcnow
from app.common.logging import get_logger

log = get_logger("risk.kill_switch")


class KillSwitch:
    def __init__(self, initial: bool = False):
        self._on = bool(initial)
        self._reason: str | None = None
        self._since = utcnow()

    def is_on(self) -> bool:
        return self._on

    @property
    def reason(self) -> str | None:
        return self._reason

    def turn_on(self, reason: str = "manual") -> None:
        if not self._on:
            log.warning("kill_switch_on", reason=reason)
        self._on = True
        self._reason = reason
        self._since = utcnow()

    def turn_off(self, reason: str = "manual") -> None:
        if self._on:
            log.warning("kill_switch_off", reason=reason)
        self._on = False
        self._reason = None
        self._since = utcnow()

    def status(self) -> dict:
        return {"on": self._on, "reason": self._reason, "since": self._since.isoformat()}
