"""
Coordinator that turns an approved opportunity into a two-leg hedge.
Handles submission, tracking, PnL, and repair.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from app.common.clock import utcnow
from app.common.enums import HedgeState, OrderStatus, OrderType, Side
from app.common.ids import new_client_order_id, new_hedge_group_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.execution.execution_policy import pick_order_type, protected_limit_price
from app.execution.order_router import OrderRouter
from app.execution.order_tracker import OrderTracker
from app.execution.repair_engine import RepairEngine
from app.execution.state_machine import HedgeStateMachine
from app.models.hedge import HedgeGroupState
from app.models.opportunity import ArbitrageOpportunity
from app.models.order import OrderIntent
from app.risk.exposure_manager import ExposureManager

log = get_logger("execution.coordinator")


class HedgeCoordinator:
    def __init__(
        self,
        settings: Settings,
        router: OrderRouter,
        tracker: OrderTracker,
        repair: RepairEngine,
        exposure: ExposureManager,
    ):
        self._settings = settings
        self._router = router
        self._tracker = tracker
        self._repair = repair
        self._exposure = exposure
        self._hedges: dict[str, HedgeGroupState] = {}

    def all_hedges(self) -> list[HedgeGroupState]:
        return list(self._hedges.values())

    def active_hedges(self) -> list[HedgeGroupState]:
        return [h for h in self._hedges.values() if not HedgeStateMachine.terminal(h.state)]

    def get(self, hid: str) -> HedgeGroupState | None:
        return self._hedges.get(hid)

    async def execute(
        self,
        opp: ArbitrageOpportunity,
        approved_amount: Decimal,
    ) -> HedgeGroupState:
        hid = new_hedge_group_id()
        now = utcnow()
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
        )
        self._hedges[hid] = group

        HedgeStateMachine.assert_transition(group.state, HedgeState.PLANNING)
        group.state = HedgeState.PLANNING

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

        notional = approved_amount * (opp.buy_price or Decimal(1))
        self._exposure.add(opp.buy_exchange, notional, hid)
        self._exposure.add(opp.sell_exchange, notional, hid)

        try:
            buy_state, sell_state = await asyncio.gather(
                self._router.submit(buy_intent),
                self._router.submit(sell_intent),
            )
        except Exception as e:  # noqa: BLE001
            log.error("submit_failed", hedge_group_id=hid, error=str(e))
            group.notes.append(f"submit_failed: {e}")
            group.state = HedgeState.ABORTED
            self._exposure.release(opp.buy_exchange, notional, hid)
            self._exposure.release(opp.sell_exchange, notional, hid)
            group.updated_at = utcnow()
            return group

        self._tracker.add(buy_state)
        self._tracker.add(sell_state)
        group.buy_order_id = buy_state.internal_order_id
        group.sell_order_id = sell_state.internal_order_id

        HedgeStateMachine.assert_transition(group.state, HedgeState.HEDGING)
        group.state = HedgeState.HEDGING
        group.executed_buy_amount = buy_state.filled
        group.executed_sell_amount = sell_state.filled
        group.net_position_base = buy_state.filled - sell_state.filled

        # Compute realized pnl (approximate for MVP; uses avg fill prices)
        buy_cost = (buy_state.avg_fill_price or Decimal(0)) * buy_state.filled
        sell_proceeds = (sell_state.avg_fill_price or Decimal(0)) * sell_state.filled
        fees = (buy_state.fee_amount or Decimal(0)) + (sell_state.fee_amount or Decimal(0))
        group.realized_pnl_quote = sell_proceeds - buy_cost - fees

        needs_repair = (
            buy_state.status != OrderStatus.FILLED
            or sell_state.status != OrderStatus.FILLED
            or abs(group.net_position_base) > Decimal("0.00000001")
        )
        if needs_repair:
            HedgeStateMachine.assert_transition(group.state, HedgeState.FAILED_NEEDS_REPAIR)
            group.state = HedgeState.FAILED_NEEDS_REPAIR
            group = await self._repair.repair(group)
        else:
            HedgeStateMachine.assert_transition(group.state, HedgeState.COMPLETED)
            group.state = HedgeState.COMPLETED

        self._exposure.release(opp.buy_exchange, notional, hid)
        self._exposure.release(opp.sell_exchange, notional, hid)
        group.updated_at = utcnow()
        log.info(
            "hedge_group_finished",
            hedge_group_id=hid,
            state=group.state.value,
            executed_buy=str(group.executed_buy_amount),
            executed_sell=str(group.executed_sell_amount),
            realized_pnl=str(group.realized_pnl_quote),
        )
        return group
