"""Deterministic state transitions for HedgeGroup and UnifiedOrderState."""

from __future__ import annotations

from app.common.enums import HedgeState, OrderStatus
from app.common.exceptions import PermanentError

_HEDGE_TRANSITIONS: dict[HedgeState, set[HedgeState]] = {
    HedgeState.NEW: {HedgeState.PLANNING, HedgeState.ABORTED},
    HedgeState.PLANNING: {HedgeState.SUBMITTING, HedgeState.ABORTED},
    HedgeState.SUBMITTING: {HedgeState.HEDGING, HedgeState.FAILED_NEEDS_REPAIR, HedgeState.ABORTED},
    HedgeState.HEDGING: {
        HedgeState.COMPLETED,
        HedgeState.FAILED_NEEDS_REPAIR,
        HedgeState.ABORTED,
    },
    HedgeState.FAILED_NEEDS_REPAIR: {HedgeState.REPAIRING, HedgeState.ABORTED},
    HedgeState.REPAIRING: {HedgeState.COMPLETED, HedgeState.FAILED_NEEDS_REPAIR, HedgeState.ABORTED},
    HedgeState.COMPLETED: set(),
    HedgeState.ABORTED: set(),
}


class HedgeStateMachine:
    @staticmethod
    def can_transition(old: HedgeState, new: HedgeState) -> bool:
        return new in _HEDGE_TRANSITIONS.get(old, set())

    @staticmethod
    def assert_transition(old: HedgeState, new: HedgeState) -> None:
        if not HedgeStateMachine.can_transition(old, new):
            raise PermanentError(f"illegal hedge transition {old} -> {new}")

    @staticmethod
    def terminal(state: HedgeState) -> bool:
        return state in (HedgeState.COMPLETED, HedgeState.ABORTED)


_ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
    OrderStatus.SUBMITTED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.CANCEL_REQUESTED: {OrderStatus.CANCELLED, OrderStatus.FILLED, OrderStatus.UNKNOWN},
    OrderStatus.CANCELLED: set(),
    OrderStatus.FILLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
    OrderStatus.UNKNOWN: set(OrderStatus),
}


class OrderStateMachine:
    @staticmethod
    def can_transition(old: OrderStatus, new: OrderStatus) -> bool:
        if old == new:
            return True
        return new in _ORDER_TRANSITIONS.get(old, set())

    @staticmethod
    def terminal(status: OrderStatus) -> bool:
        return status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )
