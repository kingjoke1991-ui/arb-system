"""Domain-level exceptions."""

from __future__ import annotations


class ArbError(Exception):
    """Base class for all domain exceptions."""


class ConfigError(ArbError):
    pass


class TransientError(ArbError):
    """Errors that may be retried."""


class PermanentError(ArbError):
    """Errors that should NOT be retried."""


class ExchangeRejectError(PermanentError):
    pass


class RateLimitError(TransientError):
    pass


class AuthError(PermanentError):
    pass


class StaleDataError(TransientError):
    pass


class UnknownOrderStateError(ArbError):
    """State machine cannot confirm what happened to an order."""


class RiskRejectError(PermanentError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class ModeViolationError(PermanentError):
    pass


class KillSwitchActiveError(PermanentError):
    pass
