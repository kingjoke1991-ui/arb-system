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
        marketdata_mode: str = "auto",
    ):
        self._books: dict[tuple[str, str], _Entry] = {}
        self._tasks: list[asyncio.Task] = []
        self._max_stale_ms = max_stale_ms
        self._poll_interval_ms = poll_interval_ms
        self._subscribers: list[asyncio.Queue[OrderBookSnapshot]] = []
        self._running = False
        # "auto"     : prefer WS, fall back to REST when ws unsupported / fails
        # "websocket": WS-only (REST fallback still triggers on ws failure)
        # "rest"     : never use WS even if available (debug / fallback knob)
        self._marketdata_mode = marketdata_mode
        # Per-(exchange, symbol) data source, surfaced for UI / debugging
        # via ``data_source()``. "ws" once a successful watch has happened,
        # "rest" otherwise.
        self._sources: dict[tuple[str, str], str] = {}

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

    def age_ms(self, exchange: str, symbol: str, now_ms: int | None = None) -> int | None:
        """Return the age in ms of the latest snapshot for ``(exchange, symbol)``.

        ``None`` means we never received a snapshot at all (treat as stale).
        Used by per-trade book-age gating, which is a tighter bound than
        the health-level staleness threshold.
        """
        entry = self._books.get((exchange, symbol))
        if not entry:
            return None
        now = now_ms if now_ms is not None else utcnow_ms()
        return max(0, now - entry.received_at_ms)

    def all_pairs(self) -> list[tuple[str, str]]:
        return list(self._books.keys())

    def latest_snapshots(self) -> list[OrderBookSnapshot]:
        return [e.snapshot for e in self._books.values()]

    def data_source(self, exchange: str, symbol: str) -> str:
        """Returns "ws" / "rest" / "unknown" for the given pair. Used by
        /health/exchanges to surface data source per exchange in the UI.
        """
        return self._sources.get((exchange, symbol), "unknown")

    def data_sources_summary(self) -> dict[str, dict[str, int]]:
        """Aggregates ``data_source`` per exchange across all symbols.
        Returns ``{exchange: {ws: N, rest: M}}`` for the health card.
        """
        out: dict[str, dict[str, int]] = {}
        for (ex, _sym), src in self._sources.items():
            bucket = out.setdefault(ex, {"ws": 0, "rest": 0})
            if src in bucket:
                bucket[src] += 1
        return out

    # --- polling ---
    async def run(
        self,
        adapters: list[ExchangeAdapter],
        symbols: list[str],
    ) -> None:
        self._running = True
        tasks = []
        skipped: list[tuple[str, str]] = []
        for a in adapters:
            for s in symbols:
                # Skip (exchange, symbol) combinations that the exchange
                # plainly does not list. adapter.supports_symbol() is
                # populated by ccxt's load_markets inside connect(); the
                # default implementation returns True (optimistic) so
                # mock / non-ccxt adapters still work.
                try:
                    if not a.supports_symbol(s):
                        skipped.append((a.name, s))
                        continue
                except Exception:  # noqa: BLE001
                    # Defensive: if supports_symbol misbehaves, don't skip —
                    # let the polling loop reveal the real error.
                    pass
                tasks.append(asyncio.create_task(self._poll_one(a, s)))
        if skipped:
            # Single compact log instead of hundreds of per-cycle warnings.
            per_ex: dict[str, list[str]] = {}
            for ex, sym in skipped:
                per_ex.setdefault(ex, []).append(sym)
            for ex, syms in per_ex.items():
                log.info(
                    "orderbook_poll_skip_unsupported",
                    exchange=ex,
                    count=len(syms),
                    symbols=",".join(syms),
                )
        self._tasks = tasks
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    def _ws_eligible(self, adapter: ExchangeAdapter) -> bool:
        if self._marketdata_mode == "rest":
            return False
        try:
            return adapter.supports_websocket()
        except Exception:  # noqa: BLE001
            return False

    async def _poll_one(self, adapter: ExchangeAdapter, symbol: str) -> None:
        """Per (exchange, symbol) consumer task.

        Strategy: prefer WS (push, sub-100ms latency). If WS errors out
        repeatedly OR the adapter doesn't support it, fall back to REST
        polling (current behavior, ~poll_interval_ms cadence). On
        ``marketdata_mode == "rest"`` we skip WS entirely. After a
        cooldown we retry WS so transient disconnects auto-recover.
        """
        interval = self._poll_interval_ms / 1000.0
        backoff = interval
        ws_disabled_until_loop = 0  # cooldown counter for ws after failures
        ws_failures = 0
        while self._running:
            use_ws = self._ws_eligible(adapter) and ws_disabled_until_loop <= 0
            try:
                if use_ws:
                    snap = await adapter.watch_orderbook_ws(symbol)
                    self._sources[(adapter.name, symbol)] = "ws"
                    self.update(snap)
                    ws_failures = 0
                    backoff = interval
                    # WS resolves on every push; no sleep needed (adapter
                    # blocks until next update).
                    continue
                else:
                    snap = await adapter.watch_orderbook(symbol)
                    self._sources[(adapter.name, symbol)] = "rest"
                    self.update(snap)
                    backoff = interval
                    if ws_disabled_until_loop > 0:
                        ws_disabled_until_loop -= 1
            except asyncio.CancelledError:
                raise
            except NotImplementedError:
                # Adapter advertised supports_websocket() but watch_orderbook_ws
                # not implemented — disable ws permanently for this pair.
                ws_disabled_until_loop = 10**9
                continue
            except TransientError as e:
                if use_ws:
                    ws_failures += 1
                    log.warning(
                        "orderbook_ws_transient_error",
                        exchange=adapter.name,
                        symbol=symbol,
                        error=str(e),
                        ws_failures=ws_failures,
                    )
                    if ws_failures >= 3:
                        # 30 polls of REST cooldown (~6s at 200ms) before retrying ws
                        ws_disabled_until_loop = 30
                        ws_failures = 0
                        log.warning(
                            "orderbook_ws_fallback_to_rest",
                            exchange=adapter.name,
                            symbol=symbol,
                        )
                else:
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
                    via="ws" if use_ws else "rest",
                    error=str(e),
                )
                if use_ws:
                    ws_disabled_until_loop = 30
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
        # Short-circuit unsupported (exchange, symbol) combinations the
        # same way the initial run() loop does.
        try:
            if not adapter.supports_symbol(symbol):
                return
        except Exception:  # noqa: BLE001
            pass
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
