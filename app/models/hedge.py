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
