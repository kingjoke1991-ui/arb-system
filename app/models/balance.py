from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True)
class BalanceSnapshot:
    exchange: str
    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal
    ts_local: datetime
