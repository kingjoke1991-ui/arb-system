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
    "order_type_policy",
    "ioc_price_buffer_bps",
    "scan_interval_ms",
    "alert_min_severity",
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
            new = self._coerce(k, v)
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
