"""
OpportunityScanner: for each (symbol, exchange_pair, direction), computes SpreadEstimate,
filters by min-edge and min-profit, emits ArbitrageOpportunity objects.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import AsyncIterator, Callable

from app.accounts.balance_manager import BalanceManager
from app.common.clock import utcnow
from app.common.ids import new_opportunity_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.opportunity import ArbitrageOpportunity
from app.strategy.spread_calculator import SpreadCalculator

log = get_logger("strategy.scanner")


class OpportunityScanner:
    def __init__(
        self,
        settings: Settings,
        book_mgr: OrderBookManager,
        balance_mgr: BalanceManager,
        calc: SpreadCalculator,
        on_opportunity: Callable[[ArbitrageOpportunity], "asyncio.Future | None"] | None = None,
    ):
        self._settings = settings
        self._books = book_mgr
        self._balances = balance_mgr
        self._calc = calc
        self._on_opportunity = on_opportunity
        self._running = False

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running

    async def run(self, exchanges: list[str]) -> None:
        """Poll loop — for every pair in the whitelist, evaluate both directions."""
        self.start()
        interval = self._settings.scan_interval_ms / 1000.0
        while self.is_running():
            try:
                for symbol in self._settings.enabled_symbol_list:
                    await self._scan_symbol(symbol, exchanges)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("scanner_loop_error", error=str(e))
            await asyncio.sleep(interval)

    async def scan_once(self, exchanges: list[str]) -> list[ArbitrageOpportunity]:
        out: list[ArbitrageOpportunity] = []
        for symbol in self._settings.enabled_symbol_list:
            out.extend(await self._scan_symbol(symbol, exchanges))
        return out

    async def _scan_symbol(self, symbol: str, exchanges: list[str]) -> list[ArbitrageOpportunity]:
        if len(exchanges) < 2:
            return []
        out: list[ArbitrageOpportunity] = []
        for i in range(len(exchanges)):
            for j in range(len(exchanges)):
                if i == j:
                    continue
                buy_ex, sell_ex = exchanges[i], exchanges[j]
                buy_book = self._books.get(buy_ex, symbol)
                sell_book = self._books.get(sell_ex, symbol)
                if not buy_book or not sell_book:
                    continue
                if self._books.is_stale(buy_ex, symbol) or self._books.is_stale(sell_ex, symbol):
                    continue

                probe = self._probe_size(symbol, buy_book, sell_book)
                est = self._calc.evaluate_direction(symbol, buy_book, sell_book, probe)
                if est is None:
                    continue
                if est.net_edge_bps < self._settings.min_net_edge_bps:
                    continue
                if est.expected_profit_quote < self._settings.min_profit_quote:
                    continue

                opp = ArbitrageOpportunity(
                    opportunity_id=new_opportunity_id(),
                    symbol=symbol,
                    buy_exchange=buy_ex,
                    sell_exchange=sell_ex,
                    buy_price=est.buy_leg.effective_price,
                    sell_price=est.sell_leg.effective_price,
                    gross_spread_bps=est.gross_spread_bps,
                    buy_fee_bps=est.buy_leg.fee_bps,
                    sell_fee_bps=est.sell_leg.fee_bps,
                    slippage_bps=est.slippage_bps_total,
                    buffer_bps=est.buffer_bps,
                    net_edge_bps=est.net_edge_bps,
                    max_tradable_size=est.max_tradable_base,
                    expected_profit_quote=est.expected_profit_quote,
                    detected_at=utcnow(),
                )
                out.append(opp)
                if self._on_opportunity is not None:
                    res = self._on_opportunity(opp)
                    if asyncio.iscoroutine(res):
                        await res
        return out

    def _probe_size(self, symbol: str, buy_book, sell_book) -> Decimal:
        """
        Start from a small probe size tied to max_notional_per_trade and go from there.
        """
        mid = buy_book.mid_price or Decimal(1)
        if mid == 0:
            mid = Decimal(1)
        probe_quote = min(self._settings.max_notional_per_trade, Decimal("200"))
        return probe_quote / mid
