"""
Coordinator that turns an approved opportunity into a two-leg hedge.
Handles submission, tracking, PnL, repair, and failure rollback.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from app.adapters.registry import AdapterRegistry
from app.common.clock import utcnow
from app.common.enums import HedgeState, Mode, OrderStatus, OrderType, Side
from app.common.exceptions import TransientError
from app.common.ids import new_client_order_id, new_hedge_group_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.execution.execution_policy import pick_order_type, protected_limit_price
from app.execution.order_router import OrderRouter
from app.execution.order_tracker import OrderTracker
from app.execution.repair_engine import RepairEngine
from app.execution.state_machine import HedgeStateMachine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.hedge import HedgeGroupState
from app.models.opportunity import ArbitrageOpportunity
from app.models.order import OrderIntent, UnifiedOrderState
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.exposure_manager import ExposureManager
from app.strategy.spread_calculator import SpreadCalculator

log = get_logger("execution.coordinator")


_TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
}


class HedgeCoordinator:
    def __init__(
        self,
        settings: Settings,
        router: OrderRouter,
        tracker: OrderTracker,
        repair: RepairEngine,
        exposure: ExposureManager,
        breaker: CircuitBreaker | None = None,
        registry: AdapterRegistry | None = None,
        book_mgr: OrderBookManager | None = None,
        spread_calc: SpreadCalculator | None = None,
    ):
        self._settings = settings
        self._router = router
        self._tracker = tracker
        self._repair = repair
        self._exposure = exposure
        self._breaker = breaker
        self._registry = registry
        self._book_mgr = book_mgr
        self._spread_calc = spread_calc
        self._hedges: dict[str, HedgeGroupState] = {}

    def all_hedges(self) -> list[HedgeGroupState]:
        return list(self._hedges.values())

    def active_hedges(self) -> list[HedgeGroupState]:
        return [h for h in self._hedges.values() if not HedgeStateMachine.terminal(h.state)]

    def get(self, hid: str) -> HedgeGroupState | None:
        return self._hedges.get(hid)

    def register(self, group: HedgeGroupState) -> None:
        """Used by startup reconciliation to restore in-flight hedges from DB."""
        self._hedges[group.hedge_group_id] = group

    async def execute(
        self,
        opp: ArbitrageOpportunity,
        approved_amount: Decimal,
    ) -> HedgeGroupState:
        hid = new_hedge_group_id()
        now = utcnow()
        # Issue 2 — signal-to-order latency: opportunities older than
        # ``max_signal_to_order_ms`` are rejected before any exchange
        # round-trip. Stale signals are the dominant source of "why did we
        # eat 8 bps slippage on this trade".
        signal_to_order_ms: int | None = None
        if opp.detected_at is not None:
            try:
                detected = opp.detected_at
                # Tolerate naive datetimes: assume UTC.
                if detected.tzinfo is None:
                    from datetime import timezone

                    detected = detected.replace(tzinfo=timezone.utc)
                signal_to_order_ms = max(0, int((now - detected).total_seconds() * 1000))
            except Exception:  # noqa: BLE001
                signal_to_order_ms = None
        group = HedgeGroupState(
            hedge_group_id=hid,
            opportunity_id=opp.opportunity_id,
            symbol=opp.symbol,
            buy_exchange=opp.buy_exchange,
            sell_exchange=opp.sell_exchange,
            target_amount=approved_amount,
            state=HedgeState.NEW,
            created_at=now,
            updated_at=now,
            expected_profit_quote=opp.expected_profit_quote,
            buy_expected_vwap=opp.buy_price,
            sell_expected_vwap=opp.sell_price,
            latency_ms_signal_to_order=signal_to_order_ms,
        )
        self._hedges[hid] = group

        max_signal_ms = getattr(self._settings, "max_signal_to_order_ms", None)
        if (
            max_signal_ms is not None
            and signal_to_order_ms is not None
            and signal_to_order_ms > max_signal_ms
        ):
            group.state = HedgeState.ABORTED
            group.notes.append(f"signal_too_old: {signal_to_order_ms}ms > {max_signal_ms}ms")
            group.failure_reason = "signal_too_old"
            group.updated_at = utcnow()
            log.warning(
                "signal_too_old",
                hedge_group_id=hid,
                signal_to_order_ms=signal_to_order_ms,
                threshold=max_signal_ms,
            )
            return group

        HedgeStateMachine.assert_transition(group.state, HedgeState.PLANNING)
        group.state = HedgeState.PLANNING

        # Re-validate edge with latest orderbook before committing to execution
        if self._book_mgr is not None and self._spread_calc is not None:
            # Issue 5 — re-check book age, not just "is_stale".
            max_age = getattr(self._settings, "max_book_age_ms_for_trade", None)
            if max_age is not None:
                buy_age = self._book_mgr.age_ms(opp.buy_exchange, opp.symbol)
                sell_age = self._book_mgr.age_ms(opp.sell_exchange, opp.symbol)
                stale = buy_age is None or sell_age is None or buy_age > max_age or sell_age > max_age
                if stale:
                    group.state = HedgeState.ABORTED
                    group.notes.append(
                        f"book_too_old: buy_age={buy_age} sell_age={sell_age} threshold={max_age}"
                    )
                    group.failure_reason = "book_too_old"
                    group.updated_at = utcnow()
                    log.warning(
                        "book_too_old",
                        hedge_group_id=hid,
                        buy_age_ms=buy_age,
                        sell_age_ms=sell_age,
                        threshold=max_age,
                    )
                    return group

            fresh_buy = self._book_mgr.get(opp.buy_exchange, opp.symbol)
            fresh_sell = self._book_mgr.get(opp.sell_exchange, opp.symbol)
            if fresh_buy and fresh_sell:
                est = self._spread_calc.evaluate_direction(
                    opp.symbol,
                    fresh_buy,
                    fresh_sell,
                    approved_amount,
                )
                if est is None or est.net_edge_bps < self._settings.min_net_edge_bps:
                    group.state = HedgeState.ABORTED
                    group.notes.append("edge_vanished_at_execution_time")
                    group.failure_reason = "edge_vanished"
                    group.updated_at = utcnow()
                    return group
                # Issue 7 — record approved-size expectation. The scanner
                # stores its probe-size estimate; here we capture what the
                # *approved* size will actually try to capture.
                group.expected_profit_quote_at_approved_size = est.expected_profit_quote
                group.expected_edge_bps_at_approved_size = est.net_edge_bps
                group.buy_expected_vwap = est.buy_leg.effective_price
                group.sell_expected_vwap = est.sell_leg.effective_price

        order_type = pick_order_type(self._settings)
        buy_price = (
            protected_limit_price(self._settings, Side.BUY, opp.buy_price)
            if order_type in (OrderType.IOC_LIMIT, OrderType.FOK_LIMIT, OrderType.LIMIT)
            else None
        )
        sell_price = (
            protected_limit_price(self._settings, Side.SELL, opp.sell_price)
            if order_type in (OrderType.IOC_LIMIT, OrderType.FOK_LIMIT, OrderType.LIMIT)
            else None
        )

        buy_intent = OrderIntent(
            hedge_group_id=hid,
            exchange=opp.buy_exchange,
            symbol=opp.symbol,
            side=Side.BUY,
            order_type=order_type,
            price=buy_price,
            amount=approved_amount,
            client_order_id=new_client_order_id(hid, "b"),
        )
        sell_intent = OrderIntent(
            hedge_group_id=hid,
            exchange=opp.sell_exchange,
            symbol=opp.symbol,
            side=Side.SELL,
            order_type=order_type,
            price=sell_price,
            amount=approved_amount,
            client_order_id=new_client_order_id(hid, "s"),
        )

        HedgeStateMachine.assert_transition(group.state, HedgeState.SUBMITTING)
        group.state = HedgeState.SUBMITTING

        # Planning-time exposure. We release based on *actual* executed notional
        # at the end so we never under-account in-flight size.
        planning_notional = approved_amount * (opp.buy_price or Decimal(1))
        self._exposure.add(opp.buy_exchange, planning_notional, hid)
        self._exposure.add(opp.sell_exchange, planning_notional, hid)
        group.planning_notional_quote = planning_notional

        results = await asyncio.gather(
            self._router.submit(buy_intent),
            self._router.submit(sell_intent),
            return_exceptions=True,
        )
        buy_res, sell_res = results
        buy_ok = isinstance(buy_res, UnifiedOrderState)
        sell_ok = isinstance(sell_res, UnifiedOrderState)

        if self._breaker is not None:
            self._breaker.record(buy_ok)
            self._breaker.record(sell_ok)

        if not buy_ok and not sell_ok:
            log.error(
                "both_legs_failed",
                hedge_group_id=hid,
                buy_error=str(buy_res),
                sell_error=str(sell_res),
            )
            group.notes.append(f"both_legs_failed: buy={buy_res} sell={sell_res}")
            group.state = HedgeState.ABORTED
            self._release_exposure(group)
            group.updated_at = utcnow()
            return group

        # Exactly one side threw. Cancel the successful leg if it's still open,
        # else hand its already-filled quantity to the repair engine.
        if buy_ok and not sell_ok:
            assert isinstance(buy_res, UnifiedOrderState)
            log.error("sell_leg_failed", hedge_group_id=hid, error=str(sell_res))
            group.notes.append(f"sell_leg_failed: {sell_res}")
            await self._rollback_single_leg(group, buy_res, Side.BUY)
            self._release_exposure(group)
            group.updated_at = utcnow()
            return group

        if sell_ok and not buy_ok:
            assert isinstance(sell_res, UnifiedOrderState)
            log.error("buy_leg_failed", hedge_group_id=hid, error=str(buy_res))
            group.notes.append(f"buy_leg_failed: {buy_res}")
            await self._rollback_single_leg(group, sell_res, Side.SELL)
            self._release_exposure(group)
            group.updated_at = utcnow()
            return group

        # Both submitted successfully — continue with the happy path.
        assert isinstance(buy_res, UnifiedOrderState)
        assert isinstance(sell_res, UnifiedOrderState)
        buy_state: UnifiedOrderState = buy_res
        sell_state: UnifiedOrderState = sell_res
        self._tracker.add(buy_state)
        self._tracker.add(sell_state)
        group.buy_order_id = buy_state.internal_order_id
        group.sell_order_id = sell_state.internal_order_id

        HedgeStateMachine.assert_transition(group.state, HedgeState.HEDGING)
        group.state = HedgeState.HEDGING
        group.executed_buy_amount = buy_state.filled
        group.executed_sell_amount = sell_state.filled
        group.net_position_base = buy_state.filled - sell_state.filled
        group.buy_actual_vwap = buy_state.avg_fill_price
        group.sell_actual_vwap = sell_state.avg_fill_price

        buy_cost = (buy_state.avg_fill_price or Decimal(0)) * buy_state.filled
        sell_proceeds = (sell_state.avg_fill_price or Decimal(0)) * sell_state.filled
        fees = (buy_state.fee_amount or Decimal(0)) + (sell_state.fee_amount or Decimal(0))
        group.realized_pnl_quote = sell_proceeds - buy_cost - fees
        group.actual_fee_quote = fees

        # Latency: order submission was at SUBMITTING transition; pick the
        # later of the two leg ``updated_at`` times as the order-to-fill
        # endpoint. Coarse but useful for outlier detection.
        order_to_fill_ms: int | None = None
        try:
            now2 = utcnow()
            latest = max(buy_state.updated_at, sell_state.updated_at)
            if latest is not None:
                order_to_fill_ms = max(0, int((now2 - latest).total_seconds() * 1000))
        except Exception:  # noqa: BLE001
            pass
        group.latency_ms_order_to_fill = order_to_fill_ms

        needs_repair = (
            buy_state.status != OrderStatus.FILLED
            or sell_state.status != OrderStatus.FILLED
            or abs(group.net_position_base) > Decimal("0.00000001")
        )
        if needs_repair:
            group.failure_reason = group.failure_reason or "partial_fill"
            HedgeStateMachine.assert_transition(group.state, HedgeState.FAILED_NEEDS_REPAIR)
            group.state = HedgeState.FAILED_NEEDS_REPAIR
            group = await self._repair.repair(group)
        else:
            HedgeStateMachine.assert_transition(group.state, HedgeState.COMPLETED)
            group.state = HedgeState.COMPLETED

        # Settle exposure on actual executed notional.
        self._release_exposure(group)
        group.updated_at = utcnow()
        # Issue 2 — feed realized PnL into the breaker so a string of
        # losing trades trips it (the order-success counter alone misses
        # this entirely).
        if self._breaker is not None:
            self._breaker.record_pnl(group.realized_pnl_quote)
        log.info(
            "hedge_group_finished",
            hedge_group_id=hid,
            state=group.state.value,
            executed_buy=str(group.executed_buy_amount),
            executed_sell=str(group.executed_sell_amount),
            realized_pnl=str(group.realized_pnl_quote),
            repair_cost=str(group.repair_cost_quote),
        )
        return group

    async def _rollback_single_leg(
        self,
        group: HedgeGroupState,
        leg_state: UnifiedOrderState,
        leg_side: Side,
    ) -> None:
        """One leg succeeded but the other failed. Try to cancel the survivor;
        if it already filled, escalate to the repair engine so the net position
        goes back to zero."""
        # Register the survivor for observability.
        self._tracker.add(leg_state)
        if leg_side == Side.BUY:
            group.buy_order_id = leg_state.internal_order_id
            group.executed_buy_amount = leg_state.filled
        else:
            group.sell_order_id = leg_state.internal_order_id
            group.executed_sell_amount = leg_state.filled

        # Attempt cancellation if the order is still open.
        if leg_state.status not in _TERMINAL_ORDER_STATUSES and leg_state.exchange_order_id:
            adapter = self._registry.get(leg_state.exchange) if self._registry else None
            if adapter is not None:
                try:
                    await asyncio.wait_for(
                        adapter.cancel_order(leg_state.exchange_order_id, leg_state.symbol),
                        timeout=self._settings.cancel_timeout_ms / 1000,
                    )
                    log.info(
                        "survivor_leg_cancelled",
                        hedge_group_id=group.hedge_group_id,
                        side=leg_side.value,
                        exchange=leg_state.exchange,
                    )
                except (TransientError, asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                    log.warning(
                        "survivor_leg_cancel_failed",
                        hedge_group_id=group.hedge_group_id,
                        side=leg_side.value,
                        error=str(e),
                    )

        # Issue 1 — book the surviving leg's PnL contribution before
        # delegating to repair. Without this, ``realized_pnl_quote`` stays
        # ``None`` on rollback even though we actually traded.
        leg_cost = (leg_state.avg_fill_price or Decimal(0)) * leg_state.filled
        leg_fee = leg_state.fee_amount or Decimal(0)
        if leg_state.filled > 0:
            if leg_side == Side.SELL:
                group.sell_actual_vwap = leg_state.avg_fill_price
                base_delta = leg_cost - leg_fee
            else:
                group.buy_actual_vwap = leg_state.avg_fill_price
                base_delta = -leg_cost - leg_fee
            group.actual_fee_quote += leg_fee
            if group.realized_pnl_quote is None:
                group.realized_pnl_quote = base_delta
            else:
                group.realized_pnl_quote += base_delta

        # If any fill occurred, we have a net position to flatten.
        group.net_position_base = group.executed_buy_amount - group.executed_sell_amount
        if abs(group.net_position_base) > Decimal("0.00000001"):
            group.failure_reason = group.failure_reason or "single_leg_failed"
            HedgeStateMachine.assert_transition(group.state, HedgeState.FAILED_NEEDS_REPAIR)
            group.state = HedgeState.FAILED_NEEDS_REPAIR
            try:
                await self._repair.repair(group)
            except Exception as e:  # noqa: BLE001
                # Repair itself can also hit a broken venue. Log and leave
                # the group in FAILED_NEEDS_REPAIR so an operator picks it up.
                log.error(
                    "rollback_repair_failed",
                    hedge_group_id=group.hedge_group_id,
                    error=str(e),
                )
                group.notes.append(f"rollback_repair_failed: {e}")
        else:
            group.state = HedgeState.ABORTED
        # Issue 2 — feed rollback PnL into the breaker too.
        if self._breaker is not None:
            self._breaker.record_pnl(group.realized_pnl_quote)

    def _release_exposure(self, group: HedgeGroupState) -> None:
        """Release in-flight exposure — release the planning-time notional (what
        was added), so the add/release pair nets to zero."""
        notional = group.planning_notional_quote
        if notional <= 0:
            return
        self._exposure.release(group.buy_exchange, notional, group.hedge_group_id)
        self._exposure.release(group.sell_exchange, notional, group.hedge_group_id)
