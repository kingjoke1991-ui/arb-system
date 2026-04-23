"""
Reconciles internal balance cache with exchange truth. Emits system events on drift.
"""

from __future__ import annotations

from decimal import Decimal

from app.accounts.balance_manager import BalanceManager
from app.common.logging import get_logger

log = get_logger("accounts.reconcile")


class AccountReconciler:
    def __init__(self, bm: BalanceManager, drift_tolerance: Decimal = Decimal("0.00001")):
        self._bm = bm
        self._tolerance = drift_tolerance

    async def run_once(self) -> dict[str, list[dict]]:
        """
        Refresh balances and return per-exchange drift summary (empty if in sync).
        """
        before = {(b.exchange, b.asset): b.total for b in self._bm.all()}
        await self._bm.refresh_all()
        after = {(b.exchange, b.asset): b.total for b in self._bm.all()}

        drift: dict[str, list[dict]] = {}
        keys = set(before.keys()) | set(after.keys())
        for exch, asset in keys:
            prev = before.get((exch, asset), Decimal(0))
            cur = after.get((exch, asset), Decimal(0))
            if abs(cur - prev) > self._tolerance:
                drift.setdefault(exch, []).append(
                    {"asset": asset, "before": str(prev), "after": str(cur), "delta": str(cur - prev)}
                )
        if drift:
            log.warning("balance_drift_detected", drift=drift)
        return drift
