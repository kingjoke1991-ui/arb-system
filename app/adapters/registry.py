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
    from app.adapters.binance_adapter import build_binance_adapter
    from app.adapters.mock_adapter import MockExchangeAdapter
    from app.adapters.okx_adapter import build_okx_adapter

    adapters: list[ExchangeAdapter] = []
    # Always attempt to build Binance + OKX. If ccxt import fails we fall back to mocks.
    try:
        adapters.append(build_binance_adapter(settings))
        adapters.append(build_okx_adapter(settings))
    except Exception as e:  # noqa: BLE001
        log.warning("ccxt_unavailable_falling_back_to_mocks", error=str(e))
        a1 = MockExchangeAdapter(name="binance")
        a2 = MockExchangeAdapter(name="okx")
        adapters = [a1, a2]
    return AdapterRegistry(adapters)
