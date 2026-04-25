"""
Maintains per-exchange balance snapshots. Freshness matters: stale balances
are treated as unreliable by the risk engine.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from app.adapters.registry import AdapterRegistry
from app.common.clock import utcnow_ms
from app.common.logging import get_logger
from app.models.balance import BalanceSnapshot

log = get_logger("accounts.balance")


class BalanceManager:
    def __init__(
        self,
        registry: AdapterRegistry,
        refresh_interval_sec: int = 15,
        settings: object | None = None,
    ):
        self._registry = registry
        self._refresh_sec = refresh_interval_sec
        # Optional live Settings handle. When present, the run-loop consults
        # settings.mode and only fetches real balances in live mode. Kept as
        # a generic object to avoid a circular import with app.config.
        self._settings = settings
        # (exchange, asset) -> snapshot
        self._balances: dict[tuple[str, str], BalanceSnapshot] = {}
        self._last_ms: dict[str, int] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        # Optimistic reservations: (exchange, asset) -> total reserved amount.
        # Subtracted from `free` so concurrent hedges don't double-spend.
        self._reserved: dict[tuple[str, str], Decimal] = {}

    def get(self, exchange: str, asset: str) -> BalanceSnapshot | None:
        return self._balances.get((exchange, asset.upper()))

    def free(self, exchange: str, asset: str) -> Decimal:
        snap = self.get(exchange, asset)
        raw = snap.free if snap else Decimal(0)
        reserved = self._reserved.get((exchange, asset.upper()), Decimal(0))
        return max(Decimal(0), raw - reserved)

    def all(self) -> list[BalanceSnapshot]:
        return list(self._balances.values())

    def by_exchange(self, exchange: str) -> list[BalanceSnapshot]:
        return [b for b in self._balances.values() if b.exchange == exchange]

    def last_refresh_ms(self, exchange: str) -> int | None:
        return self._last_ms.get(exchange)

    def is_stale(self, exchange: str, max_staleness_sec: int) -> bool:
        last = self._last_ms.get(exchange)
        if last is None:
            return True
        return (utcnow_ms() - last) > max_staleness_sec * 1000

    async def refresh_one(self, exchange: str) -> None:
        adapter = self._registry.get(exchange)
        try:
            snaps = await adapter.fetch_balances()
        except Exception as e:  # noqa: BLE001
            log.warning("balance_refresh_failed", exchange=exchange, error=str(e))
            return
        for snap in snaps:
            self._balances[(snap.exchange, snap.asset.upper())] = snap
        self._last_ms[exchange] = utcnow_ms()

    async def refresh_all(self) -> None:
        await asyncio.gather(*(self.refresh_one(a.name) for a in self._registry.all()))

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                mode = getattr(self._settings, "mode", "live") if self._settings else "live"
                if mode == "live":
                    await self.refresh_all()
                # paper-trade / dry-run: leave the virtual balances alone.
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("balance_refresh_loop_error", error=str(e))
            await asyncio.sleep(self._refresh_sec)

    async def stop(self) -> None:
        self._running = False

    # --- paper-trade helpers ---
    def set_virtual_balance(self, exchange: str, asset: str, amount: Decimal) -> None:
        from app.common.clock import utcnow

        self._balances[(exchange, asset.upper())] = BalanceSnapshot(
            exchange=exchange,
            asset=asset.upper(),
            free=amount,
            locked=Decimal(0),
            total=amount,
            ts_local=utcnow(),
        )
        self._last_ms[exchange] = utcnow_ms()

    def reserve(self, exchange: str, asset: str, amount: Decimal) -> None:
        """Optimistically reserve balance so concurrent risk checks see reduced free."""
        key = (exchange, asset.upper())
        self._reserved[key] = self._reserved.get(key, Decimal(0)) + amount

    def release_reserve(self, exchange: str, asset: str, amount: Decimal) -> None:
        """Release a previous reservation (after hedge completes or aborts)."""
        key = (exchange, asset.upper())
        cur = self._reserved.get(key, Decimal(0))
        self._reserved[key] = max(Decimal(0), cur - amount)

    def adjust_virtual(self, exchange: str, asset: str, delta: Decimal) -> None:
        snap = self.get(exchange, asset)
        from app.common.clock import utcnow

        cur = snap.free if snap else Decimal(0)
        new = max(Decimal(0), cur + delta)
        self._balances[(exchange, asset.upper())] = BalanceSnapshot(
            exchange=exchange,
            asset=asset.upper(),
            free=new,
            locked=Decimal(0),
            total=new,
            ts_local=utcnow(),
        )
        self._last_ms[exchange] = utcnow_ms()
