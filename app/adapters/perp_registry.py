"""Registry for perpetual-futures adapters.

Kept separate from the spot ``AdapterRegistry`` so the cross-exchange
scanner, balance manager, and hedge coordinator never reach for a perp
adapter by accident — the two account worlds are distinct on every
exchange (different margin, different fee tiers, different risk limits).
Only ``FundingExecutor`` and the ``FundingRateScanner`` touch perps.
"""

from __future__ import annotations

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


def build_default_perp_registry(settings: Settings) -> PerpRegistry:
    adapters: list[PerpAdapter] = []
    try:
        b = build_binance_perp_adapter(settings)
        if b is not None:
            adapters.append(b)
        o = build_okx_perp_adapter(settings)
        if o is not None:
            adapters.append(o)
    except Exception as e:  # noqa: BLE001
        log.warning("perp_adapter_build_failed", error=str(e))
    return PerpRegistry(adapters)
