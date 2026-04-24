"""Paper-trade executor for single-exchange triangular arbitrage.

The cross-exchange HedgeCoordinator cannot be reused: we have 3 dependent
legs, not 2 independent ones. This module implements a strictly
sequential 3-leg simulator that runs only in paper-trade mode (dry-run
stops at the scanner; live is deliberately out of scope until we have a
proper multi-leg state machine with on-exchange order polling).

Contract:

* Input: a ``TriangularOpportunity`` (from the scanner's ring buffer)
  plus a probe amount in asset A (default ``max_notional_per_trade``).
* Leg 1 ("A->B") consumes asks on ``pair_ba``; leg 2 consumes asks on
  ``pair_cb`` (forward) or hits bids (reverse); leg 3 hits bids on
  ``pair_ca`` (forward) or asks (reverse). Each leg goes through
  ``PaperFillEngine`` so virtual balances are adjusted and a row shows
  up in the in-memory order history.
* If any leg fails (rejected / cancelled / zero fill), we short-circuit
  and reverse-fill any successfully-filled prior legs via the same
  PaperFillEngine, using opposite sides. The result is a bounded loss
  (slippage + fees), not a stuck position.
* Outcome: an ``ExecutionReport`` dataclass is returned and appended to
  a module-level ring buffer (last 100), plus a structured event log
  line. The API exposes ``GET /opportunities/triangular/executions``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.common.clock import utcnow
from app.common.enums import OrderStatus, OrderType, Side
from app.common.ids import new_hedge_group_id
from app.common.logging import get_logger
from app.execution.paper_fill_engine import PaperFillEngine
from app.models.order import OrderIntent, UnifiedOrderState
from app.strategy.triangular_scanner import TriangularOpportunity

log = get_logger("execution.triangular")

_RING_MAX = 100


@dataclass(slots=True)
class LegResult:
    leg_index: int
    pair: str
    side: str  # "buy" / "sell"
    intent_amount: Decimal
    filled: Decimal
    avg_price: Decimal | None
    status: str
    reason: str | None = None


@dataclass(slots=True)
class ExecutionReport:
    execution_id: str
    opportunity_id: str
    exchange: str
    direction: str
    mode: str  # "paper-trade" (only one supported today)
    probe_quote: Decimal
    legs: list[LegResult]
    rolled_back: bool
    rollback_legs: list[LegResult]
    outcome: Literal["completed", "rolled_back", "aborted"]
    realized_pnl_quote: Decimal  # end_A - probe_quote in A terms
    reason: str | None
    started_at: datetime
    finished_at: datetime


_RING: deque[ExecutionReport] = deque(maxlen=_RING_MAX)


def recent_executions(limit: int = 50) -> list[dict]:
    out: list[dict] = []
    for rep in reversed(list(_RING)):
        d = asdict(rep)
        # asdict doesn't handle Decimal/datetime — stringify for API.
        d["probe_quote"] = str(rep.probe_quote)
        d["realized_pnl_quote"] = str(rep.realized_pnl_quote)
        d["started_at"] = rep.started_at.isoformat()
        d["finished_at"] = rep.finished_at.isoformat()
        for key in ("legs", "rollback_legs"):
            for leg in d[key]:
                leg["intent_amount"] = str(leg["intent_amount"])
                leg["filled"] = str(leg["filled"])
                leg["avg_price"] = str(leg["avg_price"]) if leg["avg_price"] is not None else None
        out.append(d)
    return out[:limit]


def clear_executions() -> None:
    _RING.clear()


class TriangularExecutor:
    """Paper-mode-only 3-leg executor."""

    def __init__(self, paper: PaperFillEngine):
        self._paper = paper

    # --- public -----------------------------------------------------------

    def execute_paper(
        self,
        opp: TriangularOpportunity,
        probe_quote: Decimal,
    ) -> ExecutionReport:
        exec_id = new_hedge_group_id()  # reuse the hedge_group_id generator
        started = utcnow()
        legs: list[LegResult] = []
        rollback_legs: list[LegResult] = []

        # Resolve the 3 legs (side + pair + amount) for the selected direction.
        plan = self._plan(opp, probe_quote)

        running_qty = probe_quote  # amount of current "in-hand" asset
        actual_fills: list[UnifiedOrderState] = []

        for i, (pair, side, ref_price) in enumerate(plan, start=1):
            # Derive the intent amount in BASE units for that pair.
            # pair is formatted "BASE/QUOTE". We're either spending `running_qty`
            # of the quote (buy) or `running_qty` of the base (sell).
            base, quote = pair.upper().split("/", 1)
            if side == Side.BUY:
                # BUY leg: we spend `running_qty` units of the quote asset.
                # Convert to base amount via the reference ask price; the paper
                # engine walks the book from there (and may partial-fill).
                amount_base = running_qty / ref_price if ref_price > 0 else Decimal(0)
            else:
                # SELL leg: we sell `running_qty` units of the base asset.
                amount_base = running_qty

            if amount_base <= 0:
                legs.append(
                    LegResult(
                        leg_index=i,
                        pair=pair,
                        side=side.value,
                        intent_amount=amount_base,
                        filled=Decimal(0),
                        avg_price=None,
                        status="REJECTED",
                        reason="zero_intent_amount",
                    )
                )
                break

            intent = OrderIntent(
                hedge_group_id=exec_id,
                exchange=opp.exchange,
                symbol=pair,
                side=side,
                order_type=OrderType.IOC_LIMIT,
                amount=amount_base,
                price=ref_price,
                client_order_id=f"tri-{exec_id[:8]}-L{i}",
            )
            state = self._paper.simulate(intent)
            legs.append(_to_leg(i, state))
            actual_fills.append(state)

            # Only accept a "clean" fill (filled >= 99% of intent) to keep the
            # paper sim conservative. Partial fills on 3 dependent legs get
            # messy; V2 will refine with adaptive sizing per leg.
            if state.status != OrderStatus.FILLED or state.filled <= 0:
                # Bail out — roll back previously-filled legs.
                finished = utcnow()
                rollback_legs = self._rollback(opp.exchange, exec_id, actual_fills[:-1])
                report = ExecutionReport(
                    execution_id=exec_id,
                    opportunity_id=opp.opportunity_id,
                    exchange=opp.exchange,
                    direction=opp.direction,
                    mode="paper-trade",
                    probe_quote=probe_quote,
                    legs=legs,
                    rolled_back=bool(rollback_legs),
                    rollback_legs=rollback_legs,
                    outcome="rolled_back" if rollback_legs else "aborted",
                    realized_pnl_quote=_net_quote(rollback_legs, actual_fills[:-1], probe_quote),
                    reason=f"leg_{i}_{state.status.value}",
                    started_at=started,
                    finished_at=finished,
                )
                _RING.append(report)
                log.warning(
                    "triangular_exec_failed",
                    id=exec_id,
                    leg=i,
                    status=state.status.value,
                )
                return report

            # Compute the asset we now hold based on the leg side:
            # BUY leg: we now hold `filled` units of base; price-weighted.
            # SELL leg: we now hold `filled * avg` units of quote.
            if side == Side.BUY:
                running_qty = state.filled
            else:
                running_qty = state.filled * (state.avg_fill_price or Decimal(0))

        # All 3 legs succeeded.
        finished = utcnow()
        pnl = running_qty - probe_quote
        report = ExecutionReport(
            execution_id=exec_id,
            opportunity_id=opp.opportunity_id,
            exchange=opp.exchange,
            direction=opp.direction,
            mode="paper-trade",
            probe_quote=probe_quote,
            legs=legs,
            rolled_back=False,
            rollback_legs=[],
            outcome="completed",
            realized_pnl_quote=pnl,
            reason=None,
            started_at=started,
            finished_at=finished,
        )
        _RING.append(report)
        log.info(
            "triangular_exec_completed",
            id=exec_id,
            direction=opp.direction,
            pnl=str(pnl),
        )
        return report

    # --- internals --------------------------------------------------------

    def _plan(
        self,
        opp: TriangularOpportunity,
        probe_quote: Decimal,
    ) -> list[tuple[str, Side, Decimal]]:
        """Translate a ``TriangularOpportunity`` into an ordered leg plan."""
        # Plan format: list of (pair, side, reference_price)
        #   reference_price is used for sizing only; the paper engine walks
        #   the book to get the true VWAP.
        if opp.direction.count("->") == 3:
            # Expect either A->B->C->A or A->C->B->A
            parts = opp.direction.split("->")
            if parts[:2] == list(opp.triangle[:2]):
                # forward: A->B->C->A
                return [
                    (opp.pair_ba, Side.BUY, opp.price_leg1),
                    (opp.pair_cb, Side.BUY, opp.price_leg2),
                    (opp.pair_ca, Side.SELL, opp.price_leg3),
                ]
            else:
                # reverse: A->C->B->A
                return [
                    (opp.pair_ca, Side.BUY, opp.price_leg1),
                    (opp.pair_cb, Side.SELL, opp.price_leg2),
                    (opp.pair_ba, Side.SELL, opp.price_leg3),
                ]
        # Fallback — shouldn't happen given scanner output, but keep safe.
        return []

    def _rollback(
        self,
        exchange: str,
        exec_id: str,
        filled_legs: list[UnifiedOrderState],
    ) -> list[LegResult]:
        """Reverse each filled leg in LIFO order using the paper engine."""
        out: list[LegResult] = []
        for i, leg in enumerate(reversed(filled_legs), start=1):
            reverse_side = Side.SELL if leg.side == Side.BUY else Side.BUY
            # Reverse a BUY: sell the base we bought. Reverse a SELL: buy back
            # the base we sold.
            if reverse_side == Side.BUY:
                # Need to spend quote to repurchase. Use avg_fill_price × filled
                # as the quote budget; the paper engine will walk asks.
                amount_base = leg.filled  # target the original base qty
            else:
                amount_base = leg.filled

            intent = OrderIntent(
                hedge_group_id=exec_id,
                exchange=exchange,
                symbol=leg.symbol,
                side=reverse_side,
                order_type=OrderType.IOC_LIMIT,
                amount=amount_base,
                price=leg.avg_fill_price,
                is_repair=True,
                client_order_id=f"tri-rb-{exec_id[:8]}-L{i}",
            )
            state = self._paper.simulate(intent)
            out.append(_to_leg(i, state))
        return out


def _to_leg(index: int, state: UnifiedOrderState) -> LegResult:
    return LegResult(
        leg_index=index,
        pair=state.symbol,
        side=state.side.value,
        intent_amount=state.amount,
        filled=state.filled,
        avg_price=state.avg_fill_price,
        status=state.status.value,
        reason=None,
    )


def _net_quote(
    rollback_legs: list[LegResult],
    filled_legs: list[UnifiedOrderState],
    probe_quote: Decimal,
) -> Decimal:
    """Rough post-rollback realised PnL — negative (fees + slippage) is
    expected; a positive number means we got lucky on the bounce."""
    # If we never fully completed, PnL is estimated as:
    #   (quote we got back from rollback) - probe_quote
    # We don't try to be precise here — this is a paper-trade diagnostic.
    if not rollback_legs:
        return Decimal(0)
    # The last rollback leg should have returned us to asset A; sum its
    # "filled × avg_price" if it was a SELL, or ignore if BUY.
    last = rollback_legs[-1]
    if last.side == "sell" and last.avg_price is not None:
        return last.filled * last.avg_price - probe_quote
    return -probe_quote * Decimal("0.001")  # placeholder 10 bps loss estimate
