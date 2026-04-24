"""
HealthGuard: aggregates freshness of market data and balance snapshots per exchange.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.accounts.balance_manager import BalanceManager
from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager


@dataclass
class ExchangeHealth:
    exchange: str
    marketdata_ok: bool
    balance_ok: bool
    reason: str | None


class HealthGuard:
    def __init__(self, book_mgr: OrderBookManager, balance_mgr: BalanceManager, settings: Settings):
        self._books = book_mgr
        self._balances = balance_mgr
        self._settings = settings

    def check_exchange(self, exchange: str) -> ExchangeHealth:
        # Market data: all enabled symbols should not be stale.
        stale_syms = [s for s in self._settings.enabled_symbol_list if self._books.is_stale(exchange, s)]
        md_ok = len(stale_syms) == 0

        bal_ok = not self._balances.is_stale(exchange, self._settings.max_balance_staleness_sec)

        from app.common.enums import Mode

        reason = None
        if not md_ok:
            reason = f"marketdata stale for: {','.join(stale_syms)}"
        elif not bal_ok and self._settings.mode != Mode.DRY_RUN.value:
            reason = "balance snapshot stale"
        return ExchangeHealth(exchange=exchange, marketdata_ok=md_ok, balance_ok=bal_ok, reason=reason)

    def check_all(self, exchanges: list[str]) -> list[ExchangeHealth]:
        return [self.check_exchange(e) for e in exchanges]

    def all_ok(self, exchanges: list[str]) -> bool:
        from app.common.enums import Mode

        mode = self._settings.mode
        for h in self.check_all(exchanges):
            if not h.marketdata_ok:
                return False
            if mode == Mode.LIVE.value and not h.balance_ok:
                return False
        return True
