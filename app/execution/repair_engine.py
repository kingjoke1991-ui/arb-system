"""
Repair engine: if a hedge group finishes with a net_position_base != 0, submit
a corrective trade. Strictly tagged as is_repair.
"""

from __future__ import annotations

from decimal import Decimal

from app.common.enums import HedgeState, OrderType, Side
from app.common.ids import new_client_order_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.execution.order_router import OrderRouter
from app.execution.order_tracker import OrderTracker
from app.execution.state_machine import HedgeStateMachine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.hedge import HedgeGroupState
from app.models.order import OrderIntent

log = get_logger("execution.repair")


class RepairEngine:
    def __init__(
        self,
        settings: Settings,
        router: OrderRouter,
        tracker: OrderTracker,
        book_mgr: OrderBookManager,
    ):
        self._settings = settings
        self._router = router
        self._tracker = tracker
        self._books = book_mgr

    async def repair(self, group: HedgeGroupState) -> HedgeGroupState:
        net = group.net_position_base
        if net == 0:
            group.state = HedgeState.COMPLETED
            return group

        if group.repair_attempts >= self._settings.max_repair_attempts:
            group.notes.append(f"repair attempts exhausted ({group.repair_attempts})")
            group.state = HedgeState.ABORTED
            return group

        HedgeStateMachine.assert_transition(group.state, HedgeState.REPAIRING)
        group.state = HedgeState.REPAIRING
        group.repair_attempts += 1

        if net > 0:
            # net long base -> sell on sell_exchange (cheaper venue for us to offload)
            exch = group.sell_exchange
            side = Side.SELL
            amount = net
        else:
            # net short base -> buy on buy_exchange
            exch = group.buy_exchange
            side = Side.BUY
            amount = -net

        book = self._books.get(exch, group.symbol)
        ref = (book.best_bid if side == Side.SELL else book.best_ask) if book else None
        intent = OrderIntent(
            hedge_group_id=group.hedge_group_id,
            exchange=exch,
            symbol=group.symbol,
            side=side,
            order_type=OrderType.IOC_LIMIT,
            price=ref,
            amount=amount,
            client_order_id=new_client_order_id(group.hedge_group_id, "r"),
            is_repair=True,
        )
        state = await self._router.submit(intent)
        self._tracker.add(state)
        log.info(
            "repair_submitted",
            hedge_group_id=group.hedge_group_id,
            side=side.value,
            amount=str(amount),
            status=state.status.value,
            exchange=exch,
        )

        filled = state.filled
        if side == Side.BUY:
            group.executed_buy_amount += filled
        else:
            group.executed_sell_amount += filled
        group.net_position_base = group.executed_buy_amount - group.executed_sell_amount
        if abs(group.net_position_base) <= Decimal("0.00000001"):
            HedgeStateMachine.assert_transition(group.state, HedgeState.COMPLETED)
            group.state = HedgeState.COMPLETED
        else:
            HedgeStateMachine.assert_transition(group.state, HedgeState.FAILED_NEEDS_REPAIR)
            group.state = HedgeState.FAILED_NEEDS_REPAIR
        return group
