import pytest

from app.common.enums import HedgeState, OrderStatus
from app.common.exceptions import PermanentError
from app.execution.state_machine import HedgeStateMachine, OrderStateMachine


def test_hedge_transitions_happy_path():
    path = [
        HedgeState.NEW,
        HedgeState.PLANNING,
        HedgeState.SUBMITTING,
        HedgeState.HEDGING,
        HedgeState.COMPLETED,
    ]
    for a, b in zip(path, path[1:]):
        assert HedgeStateMachine.can_transition(a, b)


def test_hedge_transitions_repair_path():
    path = [
        HedgeState.NEW,
        HedgeState.PLANNING,
        HedgeState.SUBMITTING,
        HedgeState.HEDGING,
        HedgeState.FAILED_NEEDS_REPAIR,
        HedgeState.REPAIRING,
        HedgeState.COMPLETED,
    ]
    for a, b in zip(path, path[1:]):
        assert HedgeStateMachine.can_transition(a, b)


def test_hedge_illegal_transition_raises():
    with pytest.raises(PermanentError):
        HedgeStateMachine.assert_transition(HedgeState.NEW, HedgeState.COMPLETED)


def test_hedge_terminal():
    assert HedgeStateMachine.terminal(HedgeState.COMPLETED)
    assert HedgeStateMachine.terminal(HedgeState.ABORTED)
    assert not HedgeStateMachine.terminal(HedgeState.HEDGING)


def test_order_transitions():
    assert OrderStateMachine.can_transition(OrderStatus.CREATED, OrderStatus.SUBMITTED)
    assert OrderStateMachine.can_transition(OrderStatus.SUBMITTED, OrderStatus.FILLED)
    assert OrderStateMachine.can_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED)
    assert not OrderStateMachine.can_transition(OrderStatus.CANCELLED, OrderStatus.FILLED)
