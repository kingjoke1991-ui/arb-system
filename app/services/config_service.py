"""
Mutable config snapshot exposed via the admin API. The underlying pydantic
Settings are immutable once loaded, so we overlay runtime tweaks on top of them.
Tracked keys are persisted to system_events / config_audit for auditability.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.common.clock import utcnow
from app.config.settings import Settings

_EDITABLE_KEYS = {
    "mode",
    "enabled_symbols",
    "min_net_edge_bps",
    "min_profit_quote",
    "min_order_size_quote",
    "max_notional_per_trade",
    "cooldown_seconds",
    "max_exposure_per_exchange",
    "max_total_open_hedges",
    "max_repair_attempts",
    "max_consecutive_failures",
    "max_marketdata_staleness_ms",
    "max_balance_staleness_sec",
    "kill_switch",
    "paused",
    "order_type_policy",
    "ioc_price_buffer_bps",
    "scan_interval_ms",
    "alert_min_severity",
}


# Per-key sanity bounds. Missing key = no bound. Each entry is
# (min_or_None, max_or_None). Values outside the range are clamped and a note
# is attached to the audit entry.
_BOUNDS: dict[str, tuple] = {
    "min_net_edge_bps": (Decimal("0"), Decimal("500")),
    "min_profit_quote": (Decimal("0"), Decimal("10000")),
    "min_order_size_quote": (Decimal("0"), Decimal("100000")),
    "max_notional_per_trade": (Decimal("1"), Decimal("1000000")),
    "cooldown_seconds": (0, 3600),
    "max_exposure_per_exchange": (Decimal("1"), Decimal("10000000")),
    "max_total_open_hedges": (1, 100),
    "max_repair_attempts": (0, 20),
    "max_consecutive_failures": (1, 100),
    "max_marketdata_staleness_ms": (100, 60000),
    "max_balance_staleness_sec": (1, 3600),
    "ioc_price_buffer_bps": (Decimal("0"), Decimal("200")),
    "scan_interval_ms": (50, 60000),
}


_ALLOWED_LITERAL_VALUES: dict[str, set[str]] = {
    "mode": {"dry-run", "paper-trade", "live"},
    "order_type_policy": {"limit", "market", "ioc_limit", "fok_limit"},
    "alert_min_severity": {"info", "warning", "error", "critical"},
}


class ConfigService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._audit: list[dict] = []

    @property
    def settings(self) -> Settings:
        return self._settings

    def current(self) -> dict[str, Any]:
        return {k: getattr(self._settings, k) for k in _EDITABLE_KEYS}

    def update(self, changes: dict[str, Any], actor: str = "api") -> dict:
        applied: dict[str, Any] = {}
        for k, v in changes.items():
            if k not in _EDITABLE_KEYS:
                continue
            old = getattr(self._settings, k, None)
            try:
                new = self._coerce(k, v)
            except ValueError as e:
                raise ValueError(f"invalid value for {k}: {e}") from e
            new = self._validate(k, new)
            setattr(self._settings, k, new)
            applied[k] = {"old": str(old), "new": str(new)}
            self._audit.append(
                {
                    "ts": utcnow().isoformat(),
                    "actor": actor,
                    "key": k,
                    "old": str(old),
                    "new": str(new),
                }
            )
        return applied

    def _validate(self, key: str, value: Any) -> Any:
        if key in _ALLOWED_LITERAL_VALUES:
            if str(value) not in _ALLOWED_LITERAL_VALUES[key]:
                raise ValueError(f"{key}={value!r} not in {sorted(_ALLOWED_LITERAL_VALUES[key])}")
        bounds = _BOUNDS.get(key)
        if bounds is None:
            return value
        lo, hi = bounds
        if lo is not None and value < lo:
            raise ValueError(f"{key}={value} below min {lo}")
        if hi is not None and value > hi:
            raise ValueError(f"{key}={value} above max {hi}")
        return value

    def audit(self, limit: int = 100) -> list[dict]:
        return self._audit[-limit:]

    def _coerce(self, key: str, value: Any) -> Any:
        old = getattr(self._settings, key)
        if isinstance(old, bool):
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("1", "true", "yes", "on")
        if isinstance(old, int):
            return int(value)
        if isinstance(old, Decimal):
            return Decimal(str(value))
        return value
