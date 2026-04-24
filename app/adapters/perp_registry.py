"""Registry for perpetual-futures adapters.

Kept separate from the spot ``AdapterRegistry`` so the cross-exchange
scanner, balance manager, and hedge coordinator never reach for a perp
adapter by accident — the two account worlds are distinct on every
exchange (different margin, different fee tiers, different risk limits).
Only ``FundingExecutor`` and the ``FundingRateScanner`` touch perps.
"""

from __future__ import annotations

from decimal import Decimal

from app.adapters.perp_adapter import (
    PerpAdapter,
    build_binance_perp_adapter,
    build_okx_perp_adapter,
)
from app.common.logging import get_logger
from app.config.settings import Settings

log = get_logger("adapters.perp_registry")


class PerpRegistry:
    def __init__(self, adapters: list[PerpAdapter]):
        self._adapters: dict[str, PerpAdapter] = {a.name: a for a in adapters}

    def get(self, spot_name: str) -> PerpAdapter | None:
        """Look up the perp adapter by its corresponding spot name.

        ``"binance"`` → ``binance-perp`` adapter, ``"okx"`` →
        ``okx-perp`` adapter. Returns ``None`` if not registered.
        """
        return self._adapters.get(f"{spot_name}-perp")

    def names(self) -> list[str]:
        return list(self._adapters.keys())

    def all(self) -> list[PerpAdapter]:
        return list(self._adapters.values())

    async def connect_all(self) -> None:
        for a in self._adapters.values():
            try:
                await a.connect()
            except Exception as e:  # noqa: BLE001
                log.warning("perp_connect_failed", name=a.name, error=str(e))

    async def close_all(self) -> None:
        for a in self._adapters.values():
            try:
                await a.close()
            except Exception as e:  # noqa: BLE001
                log.warning("perp_close_error", name=a.name, error=str(e))


def _build_generic_perp(
    settings: Settings, spot_id: str, ccxt_id: str, default_type: str, requires_passphrase: bool
) -> PerpAdapter | None:
    """Generic perp adapter builder for exchanges that follow the standard
    pattern (single ccxt class, select derivatives via defaultType option)."""
    try:
        import ccxt  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    klass = getattr(ccxt, ccxt_id, None)
    if klass is None:
        log.warning("perp_ccxt_class_missing", exchange=spot_id, ccxt_id=ccxt_id)
        return None

    opts: dict = {
        "apiKey": getattr(settings, f"{spot_id}_perp_api_key", "") or "",
        "secret": getattr(settings, f"{spot_id}_perp_api_secret", "") or "",
        "enableRateLimit": True,
        "options": {"defaultType": default_type},
    }
    if requires_passphrase:
        opts["password"] = getattr(settings, f"{spot_id}_perp_passphrase", "") or ""

    try:
        client = klass(opts)
    except Exception as e:  # noqa: BLE001
        log.warning("perp_client_init_failed", exchange=spot_id, error=str(e))
        return None

    # Lower default fee for perps (most exchanges charge less on derivatives).
    return PerpAdapter(name=f"{spot_id}-perp", ccxt_client=client, default_fee_bps=Decimal("5"))


def build_default_perp_registry(settings: Settings) -> PerpRegistry:
    """Build perps for every catalog entry that declares supports_perp=True.

    Uses the dedicated ``build_binance_perp_adapter`` / ``build_okx_perp_adapter``
    for those two (they use a different ccxt class for perps) and a generic
    builder for the rest.
    """
    from app.adapters.exchanges_catalog import SUPPORTED_EXCHANGES

    adapters: list[PerpAdapter] = []
    for spec in SUPPORTED_EXCHANGES:
        if not spec.supports_perp:
            continue
        try:
            if spec.id == "binance":
                a = build_binance_perp_adapter(settings)
            elif spec.id == "okx":
                a = build_okx_perp_adapter(settings)
            else:
                # Most exchanges (bybit / gate / kucoin / bitget / htx) serve
                # both spot and perp via the same ccxt class; defaultType
                # selects the market.
                default_type = "swap" if spec.id in ("okx", "gate", "kucoin", "bitget", "htx") else "future"
                a = _build_generic_perp(
                    settings,
                    spot_id=spec.id,
                    ccxt_id=spec.ccxt_perp_id or spec.ccxt_id,
                    default_type=default_type,
                    requires_passphrase=spec.perp_requires_passphrase,
                )
            if a is not None:
                adapters.append(a)
        except Exception as e:  # noqa: BLE001
            log.warning("perp_adapter_build_failed", exchange=spec.id, error=str(e))
    return PerpRegistry(adapters)
