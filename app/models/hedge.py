from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.common.enums import HedgeState


@dataclass(slots=True)
class HedgeGroupState:
    hedge_group_id: str
    opportunity_id: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    target_amount: Decimal
    state: HedgeState
    created_at: datetime
    updated_at: datetime
    buy_order_id: str | None = None
    sell_order_id: str | None = None
    executed_buy_amount: Decimal = Decimal(0)
    executed_sell_amount: Decimal = Decimal(0)
    net_position_base: Decimal = Decimal(0)
    realized_pnl_quote: Decimal | None = None
    expected_profit_quote: Decimal | None = None
    repair_attempts: int = 0
    notes: list[str] = field(default_factory=list)
    # In-flight exposure added by the coordinator at planning time; mirrored
    # at release time so add/release always net to zero even if fills diverge.
    planning_notional_quote: Decimal = Decimal(0)
    # ---- Trade-quality fields (issues 1, 6, 7) ----
    # Cumulative per-group repair cost in quote ccy: sum of (signed price *
    # filled - fee) across all repair fills. Negative = repair lost money
    # (the common case).
    repair_cost_quote: Decimal = Decimal(0)
    # Expected profit recomputed on the *approved* size with a fresh book
    # at execution-entry time. Differs from ``expected_profit_quote``
    # (scanner's probe-size estimate) when risk reduces the size or the
    # book has moved.
    expected_profit_quote_at_approved_size: Decimal | None = None
    expected_edge_bps_at_approved_size: Decimal | None = None
    # Expected per-leg VWAP (= effective price the spread calculator saw).
    buy_expected_vwap: Decimal | None = None
    sell_expected_vwap: Decimal | None = None
    # Actual per-leg average fill price.
    buy_actual_vwap: Decimal | None = None
    sell_actual_vwap: Decimal | None = None
    # Fees actually paid across both legs + repair, in quote ccy.
    actual_fee_quote: Decimal = Decimal(0)
    # Latency split: signal_to_order = HedgeCoordinator.execute() entry -
    # opp.detected_at; order_to_fill = max(leg fill ts) - submission ts.
    latency_ms_signal_to_order: int | None = None
    latency_ms_order_to_fill: int | None = None
    # If the group ended in failed/aborted with a recognizable cause,
    # written here for the trade-quality report.
    failure_reason: str | None = None
