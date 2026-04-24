"""Abstract exchange adapter contract. All exchanges return unified internal models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from app.models.balance import BalanceSnapshot
from app.models.order import OrderIntent, UnifiedOrderState
from app.models.orderbook import OrderBookSnapshot


class ExchangeAdapter(ABC):
    name: str = "abstract"

    @property
    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def watch_orderbook(self, symbol: str) -> OrderBookSnapshot: ...

    @abstractmethod
    async def fetch_balances(self) -> list[BalanceSnapshot]: ...

    @abstractmethod
    async def create_order(self, intent: OrderIntent) -> UnifiedOrderState: ...

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str, symbol: str) -> UnifiedOrderState: ...

    @abstractmethod
    async def fetch_order(self, exchange_order_id: str, symbol: str) -> UnifiedOrderState: ...

    # --- Optional helpers with sensible defaults ---
    def supports_symbol(self, symbol: str) -> bool:
        """Whether this exchange lists the given unified symbol.

        Default returns True (optimistic); CCXT-backed adapters override this
        after ``load_markets`` so the polling loop can skip symbols the
        exchange doesn't offer (e.g. PEPE/USDT on Kraken), avoiding spam
        logs and wasted request-weight.
        """
        return True

    @abstractmethod
    def fee_rate(self, symbol: str, side: str) -> Decimal:
        """Taker fee rate as a factor (e.g. 0.001 = 10bps). Overridable per symbol."""
