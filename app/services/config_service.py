"""
Mutable config snapshot exposed via the admin API. The underlying pydantic
Settings are immutable once loaded, so we overlay runtime tweaks on top of them.
Tracked keys are persisted to ``config_snapshots`` (single row) so that operator
edits survive container restarts, and an append-only audit goes to
``config_audit`` / system_events.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.common.clock import utcnow
from app.common.logging import get_logger
from app.config.settings import Settings

if TYPE_CHECKING:
    from app.storage.repositories.config_snapshots import ConfigSnapshotRepo

log = get_logger("services.config")

_EDITABLE_KEYS = {
    "mode",
    "enabled_symbols",
    "symbol_tier1",
    "symbol_tier2",
    "symbol_tier3",
    "symbol_tier1_enabled",
    "symbol_tier2_enabled",
    "symbol_tier3_enabled",
    "min_net_edge_bps",
    "min_profit_quote",
    "fee_override_bps",
    "scan_buffer_bps",
    "min_liquidity_usdt",
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
    "triangular_min_net_edge_bps",
    "funding_rate_min_apr_bps",
    "execution_mode",
    "maker_offset_bps",
    "min_fill_ratio",
    "max_wait_ms",
    "hedge_timeout_ms",
    "max_price_deviation_bps",
    "maker_poll_interval_ms",
}


# Per-key sanity bounds. Missing key = no bound. Each entry is
# (min_or_None, max_or_None). Values outside the range are clamped and a note
# is attached to the audit entry.
_BOUNDS: dict[str, tuple] = {
    "min_net_edge_bps": (Decimal("0"), Decimal("500")),
    "min_profit_quote": (Decimal("0"), Decimal("10000")),
    "scan_buffer_bps": (Decimal("0"), Decimal("100")),
    "min_liquidity_usdt": (Decimal("0"), Decimal("1000000")),
    "min_order_size_quote": (Decimal("0"), Decimal("100000")),
    "triangular_min_net_edge_bps": (Decimal("0"), Decimal("500")),
    "funding_rate_min_apr_bps": (Decimal("0"), Decimal("50000")),
    "maker_offset_bps": (Decimal("0"), Decimal("200")),
    "min_fill_ratio": (Decimal("0"), Decimal("1")),
    "max_wait_ms": (100, 60000),
    "hedge_timeout_ms": (100, 60000),
    "max_price_deviation_bps": (Decimal("0"), Decimal("500")),
    "maker_poll_interval_ms": (50, 10000),
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
    "execution_mode": {"taker_taker", "maker_taker"},
}


# Strategy on/off + selected-exchanges fields are NOT in _EDITABLE_KEYS
# (they're mutated via /strategies/{id}/configure rather than /config), but
# we DO want them to survive restarts. The persistent set is the union of
# editable keys + every Settings attribute matching the ``strategy_*_enabled``
# / ``strategy_*_exchanges`` shape.
_STRATEGY_KEY_PREFIX = "strategy_"
_STRATEGY_KEY_SUFFIXES = ("_enabled", "_exchanges")


def _persistent_keys(settings: Settings) -> set[str]:
    keys = set(_EDITABLE_KEYS)
    for attr in dir(settings):
        if not attr.startswith(_STRATEGY_KEY_PREFIX):
            continue
        if any(attr.endswith(s) for s in _STRATEGY_KEY_SUFFIXES):
            keys.add(attr)
    return keys


def _to_storable(value: Any) -> Any:
    """Coerce a Settings value into a JSON-safe primitive.

    Decimals serialize as strings (so we don't lose precision), bools/ints/
    strings pass through, and None stays None. The reverse path is
    ``ConfigService._coerce`` which already handles every case we emit.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


class ConfigService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._audit: list[dict] = []
        self._repo: ConfigSnapshotRepo | None = None

    @property
    def settings(self) -> Settings:
        return self._settings

    def attach_repo(self, repo: ConfigSnapshotRepo) -> None:
        """Wire in the persistent snapshot repo (called from bootstrap once
        the database is up). After this, ``persist()`` writes-through to DB
        on every successful ``update()``.
        """
        self._repo = repo

    async def load_from_db(self) -> int:
        """Apply the latest persisted snapshot to ``self._settings``.

        Called once at boot (after Database.create_all). Unknown keys (e.g.
        a snapshot from an older schema) are ignored. Returns the number of
        attributes restored.
        """
        if self._repo is None:
            return 0
        try:
            payload = await self._repo.load()
        except Exception as e:  # noqa: BLE001
            log.warning("config_snapshot_load_failed", error=str(e))
            return 0
        if not payload:
            return 0
        applied = 0
        keys = _persistent_keys(self._settings)
        for k, raw in payload.items():
            if k not in keys:
                continue
            if not hasattr(self._settings, k):
                continue
            try:
                new = self._coerce(k, raw)
                new = self._validate(k, new)
                setattr(self._settings, k, new)
                applied += 1
            except Exception as e:  # noqa: BLE001
                log.warning("config_snapshot_skip_field", key=k, value=str(raw), error=str(e))
        log.info("config_snapshot_loaded", applied=applied, total=len(payload))
        return applied

    async def persist(self, actor: str = "api") -> None:
        """Write the full set of persistent fields back to the snapshot row.

        Idempotent: each call writes the *current* settings values for every
        persistent key. We always write the full set (rather than a delta)
        because the snapshot row has UPSERT-with-replace semantics.
        """
        if self._repo is None:
            return
        keys = _persistent_keys(self._settings)
        payload = {k: _to_storable(getattr(self._settings, k, None)) for k in keys}
        try:
            await self._repo.save(payload, actor=actor)
        except Exception as e:  # noqa: BLE001
            log.warning("config_snapshot_save_failed", error=str(e))

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

    def audit(self, limit: int = 100) -> list[dict]:
        return self._audit[-limit:]

    def _coerce(self, key: str, value: Any) -> Any:
        old = getattr(self._settings, key)
        # Nullable keys: empty string / None / 'null' / 'none' clears the value.
        if key == "fee_override_bps":
            if value is None or (isinstance(value, str) and value.strip().lower() in ("", "null", "none")):
                return None
            return Decimal(str(value))
        if isinstance(old, bool):
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("1", "true", "yes", "on")
        if isinstance(old, int):
            return int(value)
        if isinstance(old, Decimal):
            return Decimal(str(value))
        return value

    def _validate(self, key: str, value: Any) -> Any:
        if key in _ALLOWED_LITERAL_VALUES:
            if str(value) not in _ALLOWED_LITERAL_VALUES[key]:
                raise ValueError(f"{key}={value!r} not in {sorted(_ALLOWED_LITERAL_VALUES[key])}")
        if value is None:  # nullable fields bypass bound checks
            return value
        bounds = _BOUNDS.get(key)
        if bounds is None:
            return value
        lo, hi = bounds
        if lo is not None and value < lo:
            raise ValueError(f"{key}={value} below min {lo}")
        if hi is not None and value > hi:
            raise ValueError(f"{key}={value} above max {hi}")
        return value
