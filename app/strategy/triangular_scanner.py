"""
Triangular arbitrage scanner for a single exchange.

Given a triangle (A, B, C) — e.g. (USDT, BTC, ETH) — the scanner watches
three order books on one exchange:

  leg1: B / A   (spend A, receive B)     — consume best ask of B/A
  leg2: C / B   (spend B, receive C)     — consume best ask of C/B
  leg3: C / A   (sell C, receive A)      — hit best bid of C/A

Starting with ``probe_quote`` units of A, compute what we'd receive after
all three legs and compare to the starting amount. If the result is
greater than start * (1 - 3 * fee_bps / 10000 - safety_bps / 10000) we
emit a ``TriangularOpportunity``.

Also supports the reverse direction (A -> C -> B -> A) by swapping legs.

Opportunities are stored in a module-level ring buffer (last 200 entries)
and exposed via ``recent()``. Execution is deliberately out of scope for
this module; see runbook for roadmap.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from app.adapters.registry import AdapterRegistry
from app.common.clock import utcnow
from app.common.ids import new_opportunity_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager
from app.strategy.fee_model import FeeModel

log = get_logger("strategy.triangular")

_RING_MAX = 200


@dataclass(slots=True)
class TriangularOpportunity:
    opportunity_id: str
    exchange: str
    direction: str  # "A->B->C->A" or "A->C->B->A"
    triangle: tuple[str, str, str]  # (A, B, C)
    pair_ba: str  # e.g. BTC/USDT
    pair_cb: str  # e.g. ETH/BTC
    pair_ca: str  # e.g. ETH/USDT
    # Prices used for evaluation (ask for legs we BUY, bid for legs we SELL).
    price_leg1: Decimal  # ask of pair_ba (when A->B)
    price_leg2: Decimal  # ask of pair_cb (when B->C)
    price_leg3: Decimal  # bid of pair_ca (when C->A)
    probe_quote: Decimal  # starting amount of A
    end_quote: Decimal  # final amount of A after 3 legs
    gross_edge_bps: Decimal  # (end-start)/start * 10000
    fee_bps_total: Decimal
    net_edge_bps: Decimal  # gross - fees
    detected_at: datetime


class TriangularScanner:
    def __init__(
        self,
        settings: Settings,
        registry: AdapterRegistry,
        book_mgr: OrderBookManager,
        fee_model: FeeModel,
    ):
        self._settings = settings
        self._registry = registry
        self._books = book_mgr
        self._fees = fee_model
        self._running = False
        # Ring buffer of the last N opportunities we detected (any direction).
        self._ring: deque[TriangularOpportunity] = deque(maxlen=_RING_MAX)
        # Session counter — useful for the UI panel.
        self._session_count: int = 0
        # Optional callback invoked for each qualifying opportunity in
        # paper-trade mode (wired by bootstrap to TriangularExecutor).
        self._on_opportunity = None

    # ---- public ----------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def set_on_opportunity(self, cb) -> None:
        self._on_opportunity = cb

    def stop(self) -> None:
        self._running = False

    def recent(self, limit: int = 50) -> list[TriangularOpportunity]:
        items = list(self._ring)
        items.reverse()
        return items[:limit]

    def session_count(self) -> int:
        return self._session_count

    @staticmethod
    def parse_triangles(raw: str) -> list[tuple[str, str, str]]:
        """Parse the ``triangular_triangles`` setting.

        Format: semi-colon separated 3-tuples, e.g.
            ``USDT,BTC,ETH; USDT,BTC,SOL``
        Whitespace tolerated. Rows with a wrong arity are skipped.
        """
        out: list[tuple[str, str, str]] = []
        for row in raw.split(";"):
            parts = [p.strip().upper() for p in row.split(",") if p.strip()]
            if len(parts) == 3:
                out.append((parts[0], parts[1], parts[2]))
        return out

    @staticmethod
    def pairs_for(triangle: tuple[str, str, str]) -> tuple[str, str, str]:
        """Return (pair_BA, pair_CB, pair_CA) for a triangle (A, B, C)."""
        a, b, c = triangle
        return (f"{b}/{a}", f"{c}/{b}", f"{c}/{a}")

    # ---- run loop --------------------------------------------------------

    async def run(self) -> None:
        """Main loop. Runs only while the triangular strategy flag is enabled.

        Exits when the flag flips off (the caller can reschedule). We re-read
        settings every iteration so UI toggles take effect live.
        """
        self.start()
        interval = max(0.5, self._settings.scan_interval_ms / 1000.0)
        while self._running:
            if not self._settings.strategy_triangular_same_exchange_enabled:
                self._running = False
                break
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("triangular_scan_error", error=str(e))
            await asyncio.sleep(interval)

    async def _scan_once(self) -> None:
        # Strategy-level exchange selection takes precedence over the legacy
        # triangular_exchange single value.
        raw = (self._settings.strategy_triangular_same_exchange_exchanges or "").strip()
        selected = [x.strip() for x in raw.split(",") if x.strip()]
        ex = selected[0] if selected else self._settings.triangular_exchange
        try:
            adapter = self._registry.get(ex)
        except KeyError:
            log.warning("triangular_unknown_exchange", exchange=ex)
            return

        triangles = self.parse_triangles(self._settings.triangular_triangles)
        if not triangles:
            return

        # Ensure each needed pair is being polled (idempotent).
        for t in triangles:
            for p in self.pairs_for(t):
                self._books.ensure_polling(adapter, p)

        min_edge = Decimal(self._settings.triangular_min_net_edge_bps)
        probe_quote = Decimal(self._settings.max_notional_per_trade)

        for t in triangles:
            # Forward A -> B -> C -> A
            opp_fwd = self._evaluate_direction(ex, t, probe_quote, reverse=False)
            if opp_fwd and opp_fwd.net_edge_bps >= min_edge:
                self._ring.append(opp_fwd)
                self._session_count += 1
                log.info(
                    "triangular_opp",
                    id=opp_fwd.opportunity_id,
                    dir=opp_fwd.direction,
                    net_bps=str(opp_fwd.net_edge_bps),
                )
                if self._on_opportunity is not None:
                    try:
                        self._on_opportunity(opp_fwd)
                    except Exception as e:  # noqa: BLE001
                        log.error("triangular_cb_error", error=str(e))

            # Reverse A -> C -> B -> A
            opp_rev = self._evaluate_direction(ex, t, probe_quote, reverse=True)
            if opp_rev and opp_rev.net_edge_bps >= min_edge:
                self._ring.append(opp_rev)
                self._session_count += 1
                log.info(
                    "triangular_opp",
                    id=opp_rev.opportunity_id,
                    dir=opp_rev.direction,
                    net_bps=str(opp_rev.net_edge_bps),
                )
                if self._on_opportunity is not None:
                    try:
                        self._on_opportunity(opp_rev)
                    except Exception as e:  # noqa: BLE001
                        log.error("triangular_cb_error", error=str(e))

    def _evaluate_direction(
        self,
        exchange: str,
        triangle: tuple[str, str, str],
        probe_quote: Decimal,
        *,
        reverse: bool,
    ) -> TriangularOpportunity | None:
        a, b, c = triangle
        pair_ba, pair_cb, pair_ca = self.pairs_for(triangle)
        book_ba = self._books.get(exchange, pair_ba)
        book_cb = self._books.get(exchange, pair_cb)
        book_ca = self._books.get(exchange, pair_ca)
        if not (book_ba and book_cb and book_ca):
            return None
        for p in (pair_ba, pair_cb, pair_ca):
            if self._books.is_stale(exchange, p):
                return None
        if not book_ba.asks or not book_cb.asks or not book_ca.bids:
            return None
        if not book_ba.bids or not book_cb.bids or not book_ca.asks:
            return None

        ask_ba = Decimal(str(book_ba.asks[0].price))
        bid_ba = Decimal(str(book_ba.bids[0].price))
        ask_cb = Decimal(str(book_cb.asks[0].price))
        bid_cb = Decimal(str(book_cb.bids[0].price))
        ask_ca = Decimal(str(book_ca.asks[0].price))
        bid_ca = Decimal(str(book_ca.bids[0].price))

        if min(ask_ba, ask_cb, ask_ca, bid_ba, bid_cb, bid_ca) <= 0:
            return None

        if not reverse:
            # A -> B -> C -> A
            # leg1: buy B with A at ask_ba; leg2: buy C with B at ask_cb;
            # leg3: sell C for A at bid_ca.
            qty_b = probe_quote / ask_ba
            qty_c = qty_b / ask_cb
            end_a = qty_c * bid_ca
            price1, price2, price3 = ask_ba, ask_cb, bid_ca
            direction = f"{a}->{b}->{c}->{a}"
        else:
            # A -> C -> B -> A
            # leg1: buy C with A at ask_ca; leg2: sell C for B at bid_cb;
            # leg3: sell B for A at bid_ba.
            qty_c = probe_quote / ask_ca
            qty_b = qty_c * bid_cb
            end_a = qty_b * bid_ba
            price1, price2, price3 = ask_ca, bid_cb, bid_ba
            direction = f"{a}->{c}->{b}->{a}"

        gross_bps = ((end_a - probe_quote) / probe_quote) * Decimal(10000)

        # 3 legs × taker fee each. We use the fee_model to get per-leg bps.
        fee1 = self._fees.taker_bps(exchange, pair_ba if not reverse else pair_ca, "taker")
        fee2 = self._fees.taker_bps(exchange, pair_cb, "taker")
        fee3 = self._fees.taker_bps(exchange, pair_ca if not reverse else pair_ba, "taker")
        fees_total = fee1 + fee2 + fee3
        net_bps = gross_bps - fees_total

        return TriangularOpportunity(
            opportunity_id=new_opportunity_id(),
            exchange=exchange,
            direction=direction,
            triangle=triangle,
            pair_ba=pair_ba,
            pair_cb=pair_cb,
            pair_ca=pair_ca,
            price_leg1=price1,
            price_leg2=price2,
            price_leg3=price3,
            probe_quote=probe_quote,
            end_quote=end_a,
            gross_edge_bps=gross_bps,
            fee_bps_total=fees_total,
            net_edge_bps=net_bps,
            detected_at=utcnow(),
        )


def opp_to_dict(opp: TriangularOpportunity) -> dict:
    d = asdict(opp)
    d["triangle"] = list(opp.triangle)
    for k in (
        "price_leg1",
        "price_leg2",
        "price_leg3",
        "probe_quote",
        "end_quote",
        "gross_edge_bps",
        "fee_bps_total",
        "net_edge_bps",
    ):
        d[k] = str(d[k])
    d["detected_at"] = opp.detected_at.isoformat()
    return d
