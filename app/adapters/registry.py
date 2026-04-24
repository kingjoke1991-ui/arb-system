"""Registry that owns the set of active exchange adapters."""

from __future__ import annotations

from typing import Iterable

from app.adapters.base import ExchangeAdapter
from app.common.logging import get_logger

log = get_logger("adapters.registry")


class AdapterRegistry:
    def __init__(self, adapters: Iterable[ExchangeAdapter]):
        self._adapters: dict[str, ExchangeAdapter] = {a.name: a for a in adapters}

    def get(self, name: str) -> ExchangeAdapter:
        if name not in self._adapters:
            raise KeyError(f"no adapter registered for {name}")
        return self._adapters[name]

    def names(self) -> list[str]:
        return list(self._adapters.keys())

    def all(self) -> list[ExchangeAdapter]:
        return list(self._adapters.values())

    async def connect_all(self) -> None:
        for a in self._adapters.values():
            await a.connect()

    async def close_all(self) -> None:
        for a in self._adapters.values():
            try:
                await a.close()
            except Exception as e:  # noqa: BLE001
                log.warning("adapter_close_error", exchange=a.name, error=str(e))


def build_default_registry(settings) -> AdapterRegistry:
    """Build adapters for every catalog entry. Even entries without API
    keys get a public adapter built so the scanner can read their books.

    If ccxt isn't installed or a specific exchange class is missing, we fall
    back to mock adapters for ``binance`` + ``okx`` so dev / test still
    has a functional 2-venue setup."""
    from app.adapters.ccxt_factory import build_spot_adapter
    from app.adapters.exchanges_catalog import SUPPORTED_EXCHANGES
    from app.adapters.mock_adapter import MockExchangeAdapter

    adapters: list[ExchangeAdapter] = []
    for spec in SUPPORTED_EXCHANGES:
        try:
            a = build_spot_adapter(settings, spec)
        except Exception as e:  # noqa: BLE001
            log.warning("spot_adapter_build_failed", exchange=spec.id, error=str(e))
            a = None
        if a is not None:
            adapters.append(a)

    if len(adapters) < 2:
        log.warning("ccxt_unavailable_falling_back_to_mocks")
        adapters = [MockExchangeAdapter(name="binance"), MockExchangeAdapter(name="okx")]
    return AdapterRegistry(adapters)
