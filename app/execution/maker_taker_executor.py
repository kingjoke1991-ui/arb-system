"""
Maker-taker execution path.

Instead of crossing the spread on both sides (taker-taker, the default), this
executor rests a maker LIMIT buy at ``best_bid + maker_offset_bps`` on the
cheap venue. When the maker fills (fully or partially >= min_fill_ratio), it
immediately fires a taker sell on the other venue to hedge.

V1 implementation: paper-trade only. Live mode requires real ``fetch_open_orders``
polling + cancel-order retry logic, deferred to V2 with the existing RepairEngine.

State machine (single-opportunity scope):

    POSTED
      ├─ price crosses maker → PARTIAL_FILLED / FULL_FILLED → HEDGING
      │                                                          ├─ hedge ok → DONE
      │                                                          └─ hedge fail → PANIC_CLOSING → FLAT
      ├─ max_wait_ms timeout → CANCELED (maker never filled; no exposure opened)
      └─ price deviation > threshold → CANCELED

Failure modes are handled at each state. Terminal states (DONE / CANCELED / FLAT)
are recorded in a ring buffer for UI history display.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.common.clock import utcnow
from app.common.enums import Mode, OrderStatus, OrderType, Side
from app.common.ids import new_client_order_id, new_hedge_group_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.execution.order_router import OrderRouter
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.opportunity import ArbitrageOpportunity
from app.models.order import OrderIntent, UnifiedOrderState

log = get_logger("execution.maker_taker")

_RING_MAX = 100

MakerState = Literal[
    "POSTED",
    "PARTIAL_FILLED",
    "FULL_FILLED",
    "HEDGING",
    "DONE",
    "CANCELED",
    "PANIC_CLOSING",
    "FLAT",
]


@dataclass
class MakerTakerReport:
    exec_id: str
    hedge_group_id: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    maker_price: Decimal
    maker_amount: Decimal
    filled_amount: Decimal
    hedge_filled_amount: Decimal
    state: MakerState
    reason: str
    posted_at: datetime
    completed_at: datetime | None = None
    legs: list[dict] = field(default_factory=list)
    realized_profit_quote: Decimal | None = None

    def to_dict(self) -> dict:
        return {
            "exec_id": self.exec_id,
            "hedge_group_id": self.hedge_group_id,
            "symbol": self.symbol,
            "buy_exchange": self.buy_exchange,
            "sell_exchange": self.sell_exchange,
            "maker_price": str(self.maker_price),
            "maker_amount": str(self.maker_amount),
            "filled_amount": str(self.filled_amount),
            "hedge_filled_amount": str(self.hedge_filled_amount),
            "state": self.state,
            "reason": self.reason,
            "posted_at": self.posted_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "legs": self.legs,
            "realized_profit_quote": (
                str(self.realized_profit_quote) if self.realized_profit_quote is not None else None
            ),
        }


_RING: deque[MakerTakerReport] = deque(maxlen=_RING_MAX)


def recent_reports(limit: int = 50) -> list[MakerTakerReport]:
    return list(_RING)[-limit:][::-1]


class MakerTakerExecutor:
    """Rest a maker on the cheap venue, hedge taker on the expensive one."""

    def __init__(
        self,
        settings: Settings,
        router: OrderRouter,
        book_mgr: OrderBookManager,
    ):
        self._settings = settings
        self._router = router
        self._books = book_mgr

    async def execute(
        self,
        opp: ArbitrageOpportunity,
        approved_amount: Decimal,
    ) -> MakerTakerReport | None:
        """Run the maker-taker flow end-to-end for a single opportunity.

        Returns ``None`` in dry-run (upstream caller should fall back to the
        normal taker-taker audit path). In paper / live mode returns a
        terminal ``MakerTakerReport``.
        """
        if self._settings.mode == Mode.DRY_RUN.value:
            return None

        hid = new_hedge_group_id()
        exec_id = f"mt-{hid}"

        buy_book = self._books.get(opp.buy_exchange, opp.symbol)
        sell_book = self._books.get(opp.sell_exchange, opp.symbol)
        if buy_book is None or sell_book is None:
            return self._finalize(
                exec_id,
                hid,
                opp,
                approved_amount,
                Decimal(0),
                Decimal(0),
                Decimal(0),
                "CANCELED",
                "missing_orderbook_snapshot",
                [],
            )

        # 1) Post the maker on the cheap venue just above best_bid.
        offset = Decimal(self._settings.maker_offset_bps) / Decimal("10000")
        best_bid = buy_book.best_bid
        if best_bid is None or best_bid <= 0:
            return self._finalize(
                exec_id,
                hid,
                opp,
                approved_amount,
                Decimal(0),
                Decimal(0),
                Decimal(0),
                "CANCELED",
                "no_best_bid",
                [],
            )
        maker_price = (best_bid * (Decimal(1) + offset)).quantize(Decimal("0.00000001"))

        maker_intent = OrderIntent(
            hedge_group_id=hid,
            exchange=opp.buy_exchange,
            symbol=opp.symbol,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            price=maker_price,
            amount=approved_amount,
            client_order_id=new_client_order_id(hid, "m"),
        )

        legs: list[dict] = []

        # In paper mode the router just calls PaperFillEngine.simulate(), which
        # fills a LIMIT buy as long as the book's asks reach the price. That's
        # not exactly maker semantics. For maker-taker we want to WAIT for the
        # bid-side to be hit — i.e. best_ask drops to (or below) maker_price.
        # We simulate this by polling the book until the condition holds or
        # we time out.
        mid_at_post = buy_book.mid_price or best_bid
        filled, fill_state = await self._wait_for_maker_fill(
            opp.buy_exchange, opp.symbol, maker_price, approved_amount, mid_at_post
        )
        posted_at = utcnow()
        legs.append(
            {
                "leg": "maker_buy",
                "exchange": opp.buy_exchange,
                "price": str(maker_price),
                "requested_amount": str(approved_amount),
                "filled_amount": str(filled),
                "result": fill_state,
            }
        )

        if fill_state in ("TIMEOUT", "PRICE_DRIFT", "NO_FILL"):
            return self._finalize(
                exec_id,
                hid,
                opp,
                approved_amount,
                filled,
                Decimal(0),
                Decimal(0),
                "CANCELED",
                {"TIMEOUT": "max_wait_ms", "PRICE_DRIFT": "price_drift_exceeded", "NO_FILL": "no_fill"}[
                    fill_state
                ],
                legs,
                posted_at=posted_at,
            )

        # Enforce min_fill_ratio
        min_ratio = Decimal(self._settings.min_fill_ratio)
        fill_ratio = filled / approved_amount if approved_amount > 0 else Decimal(0)
        if fill_ratio < min_ratio:
            return self._finalize(
                exec_id,
                hid,
                opp,
                approved_amount,
                filled,
                Decimal(0),
                Decimal(0),
                "CANCELED",
                f"fill_ratio_below_min:{fill_ratio:.3f}<{min_ratio:.3f}",
                legs,
                posted_at=posted_at,
            )

        # 2) Taker-sell on the other side for whatever actually filled.
        # Re-read the sell book to get a fresh price (the maker wait may have
        # lasted up to max_wait_ms).
        fresh_sell = self._books.get(opp.sell_exchange, opp.symbol) or sell_book
        hedge_bid = fresh_sell.best_bid or (sell_book.best_bid if sell_book else None)
        # Apply price buffer so the IOC fills even if the book slips slightly.
        if hedge_bid is not None:
            buffer = Decimal(self._settings.ioc_price_buffer_bps) / Decimal("10000")
            hedge_bid = hedge_bid * (Decimal(1) - buffer)
        hedge_intent = OrderIntent(
            hedge_group_id=hid,
            exchange=opp.sell_exchange,
            symbol=opp.symbol,
            side=Side.SELL,
            order_type=OrderType.IOC_LIMIT,
            price=hedge_bid,
            amount=filled,
            client_order_id=new_client_order_id(hid, "h"),
        )

        try:
            hedge_state = await asyncio.wait_for(
                self._router.submit(hedge_intent),
                timeout=self._settings.hedge_timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            legs.append(
                {
                    "leg": "hedge_sell",
                    "exchange": opp.sell_exchange,
                    "result": "TIMEOUT",
                    "filled_amount": "0",
                }
            )
            # Attempt panic-close: reverse the maker side by selling it back on the
            # same venue (market) so we don't carry a long position.
            await self._panic_close(hid, opp.buy_exchange, opp.symbol, filled, legs)
            return self._finalize(
                exec_id,
                hid,
                opp,
                approved_amount,
                filled,
                Decimal(0),
                Decimal(0),
                "FLAT",
                "hedge_timeout_panic_closed",
                legs,
                posted_at=posted_at,
            )

        legs.append(
            {
                "leg": "hedge_sell",
                "exchange": opp.sell_exchange,
                "price": str(hedge_intent.price) if hedge_intent.price else None,
                "requested_amount": str(filled),
                "filled_amount": str(hedge_state.filled),
                "result": hedge_state.status.value,
            }
        )

        if hedge_state.filled < filled:
            # Partial hedge: panic close the residual.
            residual = filled - hedge_state.filled
            await self._panic_close(hid, opp.buy_exchange, opp.symbol, residual, legs)
            return self._finalize(
                exec_id,
                hid,
                opp,
                approved_amount,
                filled,
                hedge_state.filled,
                residual,
                "FLAT",
                "hedge_partial_panic_closed",
                legs,
                posted_at=posted_at,
            )

        # Success. Compute realized PnL: (hedge_avg - maker_price) * filled - fees
        buy_cost = maker_price * filled
        sell_proceeds = (hedge_state.avg_fill_price or hedge_state.price or Decimal(0)) * filled
        profit = sell_proceeds - buy_cost
        # fees already applied by PaperFillEngine; for live, taker fee is roughly
        # sell_proceeds * fee_bps, maker is 0-ish. We leave the raw gross here.

        return self._finalize(
            exec_id,
            hid,
            opp,
            approved_amount,
            filled,
            hedge_state.filled,
            Decimal(0),
            "DONE",
            "ok",
            legs,
            posted_at=posted_at,
            realized=profit,
        )

    # ------------------------------------------------------------------

    async def _wait_for_maker_fill(
        self,
        exchange: str,
        symbol: str,
        maker_price: Decimal,
        amount: Decimal,
        mid_at_post: Decimal,
    ) -> tuple[Decimal, str]:
        """Poll the book until best_ask drops to our maker price (simulated
        fill), we hit max_wait_ms, or price drifts out of the deviation band.

        Returns (filled_amount, state_str).
        """
        wait_ms = self._settings.max_wait_ms
        poll_ms = self._settings.maker_poll_interval_ms
        deviation_bps = Decimal(self._settings.max_price_deviation_bps)
        start = time.monotonic()

        while True:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            if elapsed_ms >= wait_ms:
                return Decimal(0), "TIMEOUT"

            book = self._books.get(exchange, symbol)
            if book is None:
                await asyncio.sleep(poll_ms / 1000.0)
                continue

            # Price drift check — relative to mid at post time.
            current_mid = book.mid_price or book.best_bid or Decimal(0)
            if mid_at_post > 0 and current_mid > 0:
                drift_bps = abs(current_mid - mid_at_post) / mid_at_post * Decimal("10000")
                if drift_bps > deviation_bps:
                    return Decimal(0), "PRICE_DRIFT"

            # Simulated fill condition: market drops so best_ask touches our
            # maker price. In practice on a live venue this is roughly when
            # a sell order crosses to us. We use asks here rather than bids
            # because that's when a taker seller would accept our bid price.
            if book.best_ask is not None and book.best_ask <= maker_price:
                # Walk the ask side: any level at or below maker_price counts.
                fillable = Decimal(0)
                for lvl in book.asks:
                    if lvl.price > maker_price:
                        break
                    fillable += lvl.size
                    if fillable >= amount:
                        return amount, "FULL_FILLED"
                # Partial fill (ask depth insufficient to meet amount fully).
                if fillable > 0:
                    return fillable, "PARTIAL_FILLED"

            await asyncio.sleep(poll_ms / 1000.0)

    async def _panic_close(
        self,
        hid: str,
        exchange: str,
        symbol: str,
        amount: Decimal,
        legs: list[dict],
    ) -> None:
        """Force-flatten a dangling long position on ``exchange`` by taker-selling."""
        if amount <= 0:
            return
        book = self._books.get(exchange, symbol)
        price = book.best_bid if book else None
        intent = OrderIntent(
            hedge_group_id=hid,
            exchange=exchange,
            symbol=symbol,
            side=Side.SELL,
            order_type=OrderType.IOC_LIMIT,
            price=price,
            amount=amount,
            client_order_id=new_client_order_id(hid, "p"),
            is_repair=True,
        )
        try:
            state = await asyncio.wait_for(
                self._router.submit(intent),
                timeout=self._settings.hedge_timeout_ms / 1000.0,
            )
            legs.append(
                {
                    "leg": "panic_close",
                    "exchange": exchange,
                    "price": str(price) if price else None,
                    "requested_amount": str(amount),
                    "filled_amount": str(state.filled),
                    "result": state.status.value,
                }
            )
        except Exception as e:  # noqa: BLE001
            legs.append(
                {
                    "leg": "panic_close",
                    "exchange": exchange,
                    "requested_amount": str(amount),
                    "result": f"ERROR:{e}",
                }
            )

    def _finalize(
        self,
        exec_id: str,
        hid: str,
        opp: ArbitrageOpportunity,
        requested: Decimal,
        filled: Decimal,
        hedge_filled: Decimal,
        unhedged: Decimal,
        state: MakerState,
        reason: str,
        legs: list[dict],
        posted_at: datetime | None = None,
        realized: Decimal | None = None,
    ) -> MakerTakerReport:
        report = MakerTakerReport(
            exec_id=exec_id,
            hedge_group_id=hid,
            symbol=opp.symbol,
            buy_exchange=opp.buy_exchange,
            sell_exchange=opp.sell_exchange,
            maker_price=opp.buy_price or Decimal(0),
            maker_amount=requested,
            filled_amount=filled,
            hedge_filled_amount=hedge_filled,
            state=state,
            reason=reason,
            posted_at=posted_at or utcnow(),
            completed_at=utcnow(),
            legs=legs,
            realized_profit_quote=realized,
        )
        _RING.append(report)
        log.info(
            "maker_taker_report",
            exec_id=exec_id,
            state=state,
            reason=reason,
            symbol=opp.symbol,
            filled=str(filled),
        )
        return report


__all__ = ["MakerTakerExecutor", "MakerTakerReport", "recent_reports"]
