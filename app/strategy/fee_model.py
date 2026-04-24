"""
Fee model. Taker fees only (MVP). Per-exchange + per-symbol overrides supported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.adapters.registry import AdapterRegistry


@dataclass
class FeeTable:
    default_bps: Decimal = Decimal("10")
    per_exchange_bps: dict[str, Decimal] = field(default_factory=dict)
    per_symbol_bps: dict[tuple[str, str], Decimal] = field(default_factory=dict)

    def get_bps(self, exchange: str, symbol: str) -> Decimal:
        k = (exchange, symbol.upper())
        if k in self.per_symbol_bps:
            return self.per_symbol_bps[k]
        if exchange in self.per_exchange_bps:
            return self.per_exchange_bps[exchange]
        return self.default_bps


class FeeModel:
    def __init__(
        self,
        registry: AdapterRegistry,
        table: FeeTable | None = None,
        override_bps_getter: "callable[[], Decimal | None] | None" = None,
    ):
        self._registry = registry
        self._table = table or FeeTable()
        # Lazy override so runtime settings updates take effect without
        # rebuilding the container. Called on every taker_bps() query.
        self._override_getter = override_bps_getter

    def taker_bps(self, exchange: str, symbol: str, side: str) -> Decimal:
        # 1) Runtime override wins when set (verification mode).
        if self._override_getter is not None:
            ov = self._override_getter()
            if ov is not None:
                return Decimal(ov)
        # 2) Prefer adapter-reported rate (from ccxt markets).
        try:
            adapter = self._registry.get(exchange)
            rate = adapter.fee_rate(symbol, side)
            if rate > 0:
                return rate * Decimal("10000")
        except Exception:  # noqa: BLE001
            pass
        # 3) Table default.
        return self._table.get_bps(exchange, symbol)

    def fee_cost_quote(self, exchange: str, symbol: str, side: str, notional_quote: Decimal) -> Decimal:
        return notional_quote * self.taker_bps(exchange, symbol, side) / Decimal("10000")
