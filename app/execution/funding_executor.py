"""Spot × Perpetual funding-rate hedge executor.

Given a ``FundingRateOpportunity`` (from ``FundingRateScanner``), open a
market-neutral position:

* Positive funding rate (longs pay shorts):
    - **SPOT**: BUY base (e.g. buy BTC with USDT)
    - **PERP**: SELL short perp (e.g. short BTC-PERP)
  → net delta-neutral, collect funding each 8h from longs.

* Negative funding rate (shorts pay longs):
    - **SPOT**: SELL base (assumes already-held inventory; we don't short
      on spot)
    - **PERP**: BUY long perp.

The two legs MUST be opened approximately simultaneously — any lag opens
a directional window. We use ``asyncio.gather(return_exceptions=True)``
so if one leg fails, we can immediately repair the other instead of
getting stuck with a lopsided book.

Modes:

* ``paper``: both legs go through ``PaperFillEngine``. We push the perp
  orderbook into ``OrderBookManager`` just-in-time (no continuous poll),
  then walk it for VWAP. Virtual balances are adjusted on both the spot
  and perp "venues" (the perp venue's quote is always USDT).
* ``live``: spot leg via the standard spot adapter's ``create_order``;
  perp leg via ``PerpAdapter.create_order``. On half-fill we reverse
  the filled side via another live ``create_order``.

This file does **not** implement closing the hedge — that's
``close_hedge()`` / ``maintenance_loop`` in a follow-up. V1 scope is
opening a hedge and tracking it in memory.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.accounts.balance_manager import BalanceManager
from app.adapters.base import ExchangeAdapter
from app.adapters.perp_adapter import PerpAdapter
from app.common.clock import utcnow
from app.common.enums import Mode, OrderStatus, OrderType, Side
from app.common.ids import new_hedge_group_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.execution.paper_fill_engine import PaperFillEngine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.order import OrderIntent, UnifiedOrderState
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.strategy.funding_rate_scanner import FundingRateOpportunity

log = get_logger("execution.funding")

_RING_MAX = 100


@dataclass(slots=True)
class FundingHedge:
    hedge_id: str
    opportunity_id: str
    exchange: str  # spot name (perp inferred as {exchange}-perp)
    symbol: str  # perp symbol e.g. "BTC/USDT:USDT"
    direction: str  # e.g. "short-perp-long-spot"
    mode: str  # "paper" / "live"
    spot_state: dict | None
    perp_state: dict | None
    outcome: Literal["opened", "failed", "repaired"]
    reason: str | None
    opened_at: datetime
    funding_rate_bps: Decimal
    apr_bps: Decimal


_RING: deque[FundingHedge] = deque(maxlen=_RING_MAX)


def recent_hedges(limit: int = 50) -> list[dict]:
    out = []
    for h in reversed(list(_RING)):
        d = asdict(h)
        d["opened_at"] = h.opened_at.isoformat()
        d["funding_rate_bps"] = str(h.funding_rate_bps)
        d["apr_bps"] = str(h.apr_bps)
        out.append(d)
    return out[:limit]


def clear_hedges() -> None:
    _RING.clear()


def _state_to_dict(state: UnifiedOrderState | None) -> dict | None:
    if state is None:
        return None
    return {
        "exchange": state.exchange,
        "symbol": state.symbol,
        "side": state.side.value,
        "amount": str(state.amount),
        "filled": str(state.filled),
        "avg_fill_price": str(state.avg_fill_price) if state.avg_fill_price is not None else None,
        "status": state.status.value,
    }


class FundingExecutor:
    def __init__(
        self,
        settings: Settings,
        book_mgr: OrderBookManager,
        paper: PaperFillEngine,
        balances: BalanceManager,
    ):
        self._settings = settings
        self._books = book_mgr
        self._paper = paper
        self._balances = balances
        # Spot + perp adapter lookup is wired from bootstrap to avoid
        # circular imports and keep the executor dependency-injected.
        self._spot_lookup = None  # callable(name) -> ExchangeAdapter
        self._perp_lookup = None  # callable(name) -> PerpAdapter | None

    def configure(self, spot_lookup, perp_lookup) -> None:
        self._spot_lookup = spot_lookup
        self._perp_lookup = perp_lookup

    # ----- Public ---------------------------------------------------------

    async def execute(self, opp: FundingRateOpportunity) -> FundingHedge | None:
        """Open a hedge for this opportunity, dispatching by settings.mode.

        Returns ``None`` when mode is ``dry-run`` (we only log) or when the
        strategy is disabled.
        """
        if not self._settings.strategy_funding_rate_spot_perp_enabled:
            return None
        mode = self._settings.mode
        if mode == Mode.DRY_RUN.value:
            log.info("funding_dryrun_skip", id=opp.opportunity_id)
            return None
        if mode == Mode.PAPER_TRADE.value:
            return await self._execute_paper(opp)
        if mode == Mode.LIVE.value:
            return await self._execute_live(opp)
        return None

    # ----- Paper ----------------------------------------------------------

    async def _execute_paper(self, opp: FundingRateOpportunity) -> FundingHedge:
        hedge_id = new_hedge_group_id()
        spot_symbol = opp.symbol.split(":", 1)[0]
        # Pull current perp book (best-effort) so PaperFillEngine can walk it.
        await self._ensure_perp_book(opp.exchange, opp.symbol)

        spot_side = Side.BUY if opp.direction == "short-perp-long-spot" else Side.SELL
        perp_side = Side.SELL if opp.direction == "short-perp-long-spot" else Side.BUY
        notional = Decimal(self._settings.funding_max_notional_per_trade)

        spot_book = self._books.get(opp.exchange, spot_symbol)
        if spot_book is None or not spot_book.asks or not spot_book.bids:
            return _fail(hedge_id, opp, "spot_book_missing", mode="paper")
        spot_ref = (
            Decimal(str(spot_book.asks[0].price))
            if spot_side == Side.BUY
            else Decimal(str(spot_book.bids[0].price))
        )
        amount_base = notional / spot_ref if spot_ref > 0 else Decimal(0)
        if amount_base <= 0:
            return _fail(hedge_id, opp, "zero_amount", mode="paper")

        spot_intent = OrderIntent(
            hedge_group_id=hedge_id,
            exchange=opp.exchange,
            symbol=spot_symbol,
            side=spot_side,
            order_type=OrderType.IOC_LIMIT,
            amount=amount_base,
            price=spot_ref,
            client_order_id=f"fund-{hedge_id[:8]}-S",
        )
        perp_intent = OrderIntent(
            hedge_group_id=hedge_id,
            exchange=f"{opp.exchange}-perp",
            symbol=opp.symbol,
            side=perp_side,
            order_type=OrderType.IOC_LIMIT,
            amount=amount_base,
            price=opp.perp_mark_price or spot_ref,
            client_order_id=f"fund-{hedge_id[:8]}-P",
        )

        # Both paper sims are pure-function; run sequentially.
        spot_state = self._paper.simulate(spot_intent)
        perp_state = self._paper.simulate(perp_intent)

        outcome, reason = _classify(spot_state, perp_state)
        hedge = FundingHedge(
            hedge_id=hedge_id,
            opportunity_id=opp.opportunity_id,
            exchange=opp.exchange,
            symbol=opp.symbol,
            direction=opp.direction,
            mode="paper",
            spot_state=_state_to_dict(spot_state),
            perp_state=_state_to_dict(perp_state),
            outcome=outcome,
            reason=reason,
            opened_at=utcnow(),
            funding_rate_bps=opp.funding_rate * Decimal(10000),
            apr_bps=opp.apr_bps,
        )
        _RING.append(hedge)
        log.info(
            "funding_paper_hedge",
            id=hedge_id,
            outcome=outcome,
            spot=spot_state.status.value,
            perp=perp_state.status.value,
        )
        return hedge

    async def _ensure_perp_book(self, exchange: str, symbol: str) -> None:
        if self._perp_lookup is None:
            return
        perp = self._perp_lookup(exchange)
        if perp is None:
            return
        try:
            snap = await perp.watch_orderbook(symbol)
            # Tag the snapshot with the "-perp" name so the paper engine
            # routes to the correct virtual exchange.
            snap_perp = OrderBookSnapshot(
                exchange=f"{exchange}-perp",
                symbol=symbol,
                bids=[OrderBookLevel(lv.price, lv.size) for lv in snap.bids],
                asks=[OrderBookLevel(lv.price, lv.size) for lv in snap.asks],
                ts_local=snap.ts_local,
                ts_exchange=snap.ts_exchange,
                latency_ms=snap.latency_ms,
            )
            self._books.update(snap_perp)
        except Exception as e:  # noqa: BLE001
            log.warning("perp_book_fetch_failed", exchange=exchange, symbol=symbol, error=str(e))

    # ----- Live -----------------------------------------------------------

    async def _execute_live(self, opp: FundingRateOpportunity) -> FundingHedge:
        hedge_id = new_hedge_group_id()
        if self._spot_lookup is None or self._perp_lookup is None:
            return _fail(hedge_id, opp, "executor_not_configured", mode="live")

        spot = self._spot_lookup(opp.exchange)
        perp = self._perp_lookup(opp.exchange)
        if spot is None:
            return _fail(hedge_id, opp, "spot_adapter_missing", mode="live")
        if perp is None or not perp.is_configured:
            return _fail(hedge_id, opp, "perp_adapter_missing_or_unconfigured", mode="live")

        spot_symbol = opp.symbol.split(":", 1)[0]
        spot_side = Side.BUY if opp.direction == "short-perp-long-spot" else Side.SELL
        perp_side = Side.SELL if opp.direction == "short-perp-long-spot" else Side.BUY
        notional = Decimal(self._settings.funding_max_notional_per_trade)

        spot_book = self._books.get(opp.exchange, spot_symbol)
        if spot_book is None or not spot_book.asks or not spot_book.bids:
            return _fail(hedge_id, opp, "spot_book_missing", mode="live")
        spot_ref = (
            Decimal(str(spot_book.asks[0].price))
            if spot_side == Side.BUY
            else Decimal(str(spot_book.bids[0].price))
        )
        amount_base = notional / spot_ref if spot_ref > 0 else Decimal(0)
        if amount_base <= 0:
            return _fail(hedge_id, opp, "zero_amount", mode="live")

        # Basis guard: refuse to open if spot and perp are more than
        # funding_max_basis_bps apart.
        if opp.perp_mark_price and spot_ref > 0:
            basis_bps = abs(Decimal(str(opp.perp_mark_price)) - spot_ref) / spot_ref * Decimal(10000)
            if basis_bps > Decimal(self._settings.funding_max_basis_bps):
                return _fail(
                    hedge_id,
                    opp,
                    f"basis_too_wide_{basis_bps:.0f}bps",
                    mode="live",
                )

        spot_intent = OrderIntent(
            hedge_group_id=hedge_id,
            exchange=opp.exchange,
            symbol=spot_symbol,
            side=spot_side,
            order_type=OrderType.IOC_LIMIT,
            amount=amount_base,
            price=spot_ref,
            client_order_id=f"fund-{hedge_id[:8]}-S",
        )
        perp_intent = OrderIntent(
            hedge_group_id=hedge_id,
            exchange=f"{opp.exchange}-perp",
            symbol=opp.symbol,
            side=perp_side,
            order_type=OrderType.IOC_LIMIT,
            amount=amount_base,
            price=opp.perp_mark_price or spot_ref,
            client_order_id=f"fund-{hedge_id[:8]}-P",
        )

        # Fire both concurrently — if either throws we'll inspect and repair.
        spot_res, perp_res = await asyncio.gather(
            _safe(spot.create_order(spot_intent)),
            _safe(perp.create_order(perp_intent)),
            return_exceptions=False,
        )

        # Classify.
        spot_state = spot_res if isinstance(spot_res, UnifiedOrderState) else None
        perp_state = perp_res if isinstance(perp_res, UnifiedOrderState) else None
        spot_err = spot_res if isinstance(spot_res, Exception) else None
        perp_err = perp_res if isinstance(perp_res, Exception) else None

        outcome = "opened"
        reason = None

        if spot_err or perp_err:
            # One or both failed — if only one survived, reverse it.
            survivor_state = None
            survivor_adapter: ExchangeAdapter | PerpAdapter | None = None
            survivor_intent = None
            if spot_state and spot_state.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                survivor_state, survivor_adapter, survivor_intent = spot_state, spot, spot_intent
            elif perp_state and perp_state.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                survivor_state, survivor_adapter, survivor_intent = perp_state, perp, perp_intent

            if survivor_state is not None and survivor_state.filled > 0:
                rev_intent = OrderIntent(
                    hedge_group_id=hedge_id,
                    exchange=survivor_intent.exchange,
                    symbol=survivor_intent.symbol,
                    side=Side.SELL if survivor_intent.side == Side.BUY else Side.BUY,
                    order_type=OrderType.IOC_LIMIT,
                    amount=survivor_state.filled,
                    price=survivor_state.avg_fill_price,
                    is_repair=True,
                    client_order_id=f"fund-rv-{hedge_id[:8]}",
                )
                try:
                    await survivor_adapter.create_order(rev_intent)
                    outcome = "repaired"
                except Exception as e:  # noqa: BLE001
                    outcome = "failed"
                    reason = f"repair_failed: {e}"
            else:
                outcome = "failed"
                reason = f"spot_err={spot_err} perp_err={perp_err}"
        else:
            outcome, reason = _classify(spot_state, perp_state)

        hedge = FundingHedge(
            hedge_id=hedge_id,
            opportunity_id=opp.opportunity_id,
            exchange=opp.exchange,
            symbol=opp.symbol,
            direction=opp.direction,
            mode="live",
            spot_state=_state_to_dict(spot_state),
            perp_state=_state_to_dict(perp_state),
            outcome=outcome,
            reason=reason,
            opened_at=utcnow(),
            funding_rate_bps=opp.funding_rate * Decimal(10000),
            apr_bps=opp.apr_bps,
        )
        _RING.append(hedge)
        log.info("funding_live_hedge", id=hedge_id, outcome=outcome, reason=reason)
        return hedge


def _classify(spot: UnifiedOrderState, perp: UnifiedOrderState) -> tuple[str, str | None]:
    if spot.status == OrderStatus.FILLED and perp.status == OrderStatus.FILLED:
        return "opened", None
    parts = []
    if spot.status != OrderStatus.FILLED:
        parts.append(f"spot:{spot.status.value}")
    if perp.status != OrderStatus.FILLED:
        parts.append(f"perp:{perp.status.value}")
    return "failed", ",".join(parts)


def _fail(hedge_id: str, opp: FundingRateOpportunity, reason: str, *, mode: str) -> FundingHedge:
    hedge = FundingHedge(
        hedge_id=hedge_id,
        opportunity_id=opp.opportunity_id,
        exchange=opp.exchange,
        symbol=opp.symbol,
        direction=opp.direction,
        mode=mode,
        spot_state=None,
        perp_state=None,
        outcome="failed",
        reason=reason,
        opened_at=utcnow(),
        funding_rate_bps=opp.funding_rate * Decimal(10000),
        apr_bps=opp.apr_bps,
    )
    _RING.append(hedge)
    log.warning("funding_hedge_failed", id=hedge_id, reason=reason, mode=mode)
    return hedge


async def _safe(coro):
    """Turn awaitable exceptions into return values for gather analysis."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        return e
