"""
HealthGuard: aggregates freshness of market data and balance snapshots per exchange.

Two views of "exchange health":

1. **Trade-level** (``all_ok_for_trade``, ``check_for_trade``): used by the
   risk engine when evaluating a specific cross-exchange opportunity.
   Only the **opp's symbol** matters here — if BTC/USDT's book is fresh
   on both legs, the trade should be allowed even if SHIB/USDT happens
   to be stale on one of the exchanges (or, more commonly, never gets a
   ws push because the exchange doesn't list it).

2. **Dashboard-level** (``check_exchange``, ``all_ok``): the legacy
   coarser view used by ``/health/exchanges`` for operator visibility.
   Kept for backwards compatibility with the UI panel and any callers
   that pre-date the per-symbol fix.

Pre-fix, ``rules.py`` was using the dashboard-level ``all_ok`` to gate
trades, which meant any opportunity touching kraken / coinbase / htx
(exchanges that don't list a portion of the configured alts) got
``EXCHANGE_UNHEALTHY``-rejected even when the actual trading pair was
perfectly fresh. See PR #11 for the regression fix and tests.
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

    # ------------------------------------------------------------------
    # Dashboard-level (coarse) checks
    # ------------------------------------------------------------------

    def check_exchange(self, exchange: str) -> ExchangeHealth:
        """Coarse health for the dashboard. Considers **all** enabled
        symbols on the exchange. ``marketdata_ok=False`` if any one of
        them is stale or never received."""
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
        """Coarse health gate. **Do not** use this to gate individual
        trades — use :meth:`all_ok_for_trade` instead, which scopes the
        check to the trade's actual symbol. Kept for backwards
        compatibility with non-trade callers (dashboards / tests)."""
        from app.common.enums import Mode

        mode = self._settings.mode
        for h in self.check_all(exchanges):
            if not h.marketdata_ok:
                return False
            if mode == Mode.LIVE.value and not h.balance_ok:
                return False
        return True

    # ------------------------------------------------------------------
    # Trade-level (per-symbol) checks
    # ------------------------------------------------------------------

    def check_for_trade(self, exchange: str, symbol: str) -> ExchangeHealth:
        """Per-symbol health for a specific trade leg. ``marketdata_ok``
        only reflects ``(exchange, symbol)``, not other unrelated
        symbols on the same exchange."""
        md_ok = not self._books.is_stale(exchange, symbol)
        bal_ok = not self._balances.is_stale(exchange, self._settings.max_balance_staleness_sec)

        from app.common.enums import Mode

        reason = None
        if not md_ok:
            reason = f"{symbol} stale on {exchange}"
        elif not bal_ok and self._settings.mode != Mode.DRY_RUN.value:
            reason = "balance snapshot stale"
        return ExchangeHealth(exchange=exchange, marketdata_ok=md_ok, balance_ok=bal_ok, reason=reason)

    def all_ok_for_trade(self, exchanges: list[str], symbol: str) -> bool:
        """True if every exchange in ``exchanges`` has a fresh
        orderbook for ``symbol`` (and, in live mode, a fresh balance
        snapshot)."""
        from app.common.enums import Mode

        mode = self._settings.mode
        for ex in exchanges:
            h = self.check_for_trade(ex, symbol)
            if not h.marketdata_ok:
                return False
            if mode == Mode.LIVE.value and not h.balance_ok:
                return False
        return True
