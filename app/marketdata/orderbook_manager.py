"""
OrderBookManager — keeps the latest OrderBookSnapshot per (exchange, symbol) in memory,
exposes staleness checks, and drives a polling loop against each adapter.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal

from app.adapters.base import ExchangeAdapter
from app.common.clock import utcnow_ms
from app.common.exceptions import TransientError
from app.common.logging import get_logger
from app.models.orderbook import OrderBookSnapshot

log = get_logger("marketdata.orderbook")


@dataclass
class _Entry:
    snapshot: OrderBookSnapshot
    received_at_ms: int


class OrderBookManager:
    """
    Maintains top-of-book and latest snapshot. Supports multiple concurrent polling tasks.
    """

    def __init__(
        self,
        max_stale_ms: int = 3000,
        poll_interval_ms: int = 200,
    ):
        self._books: dict[tuple[str, str], _Entry] = {}
        self._tasks: list[asyncio.Task] = []
        self._max_stale_ms = max_stale_ms
        self._poll_interval_ms = poll_interval_ms
        self._subscribers: list[asyncio.Queue[OrderBookSnapshot]] = []
        self._running = False

    def update(self, snap: OrderBookSnapshot) -> None:
        self._books[(snap.exchange, snap.symbol)] = _Entry(snap, utcnow_ms())
        for q in list(self._subscribers):
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                pass

    def get(self, exchange: str, symbol: str) -> OrderBookSnapshot | None:
        entry = self._books.get((exchange, symbol))
        return entry.snapshot if entry else None

    def is_stale(self, exchange: str, symbol: str, now_ms: int | None = None) -> bool:
        entry = self._books.get((exchange, symbol))
        if not entry:
            return True
        now = now_ms if now_ms is not None else utcnow_ms()
        return (now - entry.received_at_ms) > self._max_stale_ms

    def all_pairs(self) -> list[tuple[str, str]]:
        return list(self._books.keys())

    def latest_snapshots(self) -> list[OrderBookSnapshot]:
        return [e.snapshot for e in self._books.values()]

    # --- polling ---
    async def run(
        self,
        adapters: list[ExchangeAdapter],
        symbols: list[str],
    ) -> None:
        self._running = True
        tasks = []
        for a in adapters:
            for s in symbols:
                tasks.append(asyncio.create_task(self._poll_one(a, s)))
        self._tasks = tasks
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def _poll_one(self, adapter: ExchangeAdapter, symbol: str) -> None:
        interval = self._poll_interval_ms / 1000.0
        backoff = interval
        while self._running:
            try:
                snap = await adapter.watch_orderbook(symbol)
                self.update(snap)
                backoff = interval
            except asyncio.CancelledError:
                raise
            except TransientError as e:
                log.warning(
                    "orderbook_poll_transient_error",
                    exchange=adapter.name,
                    symbol=symbol,
                    error=str(e),
                )
                await asyncio.sleep(min(5.0, backoff))
                backoff = min(5.0, backoff * 2)
                continue
            except Exception as e:  # noqa: BLE001
                log.error(
                    "orderbook_poll_error",
                    exchange=adapter.name,
                    symbol=symbol,
                    error=str(e),
                )
                await asyncio.sleep(min(5.0, backoff))
                backoff = min(5.0, backoff * 2)
                continue
            await asyncio.sleep(interval)

    def ensure_polling(self, adapter: ExchangeAdapter, symbol: str) -> None:
        """Idempotently schedule a polling task for (adapter, symbol).

        Used by strategies (e.g. triangular) that need cross pairs outside
        the globally-configured ``enabled_symbols`` list. Safe to call repeatedly.
        """
        if not self._running:
            return
        key = f"{adapter.name}:{symbol}"
        for t in self._tasks:
            if t.get_name() == key and not t.done():
                return
        t = asyncio.create_task(self._poll_one(adapter, symbol), name=key)
        self._tasks.append(t)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    # --- subscription (for scanner) ---
    def subscribe(self, maxsize: int = 128) -> asyncio.Queue[OrderBookSnapshot]:
        q: asyncio.Queue[OrderBookSnapshot] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        return q
