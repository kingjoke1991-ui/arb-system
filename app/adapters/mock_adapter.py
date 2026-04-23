"""In-memory adapter for tests, dry-run demos, and paper-trade when no creds provided."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.adapters.base import ExchangeAdapter
from app.common.clock import utcnow
from app.common.enums import OrderStatus, OrderType, Side
from app.common.ids import new_order_id
from app.models.balance import BalanceSnapshot
from app.models.order import OrderIntent, UnifiedOrderState
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot


class MockExchangeAdapter(ExchangeAdapter):
    """
    Deterministic mock. You control the next orderbook and balances explicitly.
    Orders fill fully at requested price (or mid if market).
    """

    def __init__(self, name: str = "mock", fee_bps: Decimal = Decimal("10")):
        self.name = name
        self._fee_bps = fee_bps
        self._books: dict[str, OrderBookSnapshot] = {}
        self._balances: dict[str, Decimal] = {}
        self._orders: dict[str, UnifiedOrderState] = {}
        self._connected = False

    @property
    def is_configured(self) -> bool:
        return True

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    def set_orderbook(self, symbol: str, bids: list[tuple], asks: list[tuple]) -> None:
        self._books[symbol] = OrderBookSnapshot(
            exchange=self.name,
            symbol=symbol,
            bids=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in bids],
            asks=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in asks],
            ts_local=utcnow(),
        )

    def set_balance(self, asset: str, amount: Decimal | float | int) -> None:
        self._balances[asset] = Decimal(str(amount))

    async def watch_orderbook(self, symbol: str) -> OrderBookSnapshot:
        if symbol not in self._books:
            # default flat book
            self.set_orderbook(
                symbol,
                [(100, 1.0), (99.9, 2.0)],
                [(100.1, 1.0), (100.2, 2.0)],
            )
        return self._books[symbol]

    async def fetch_balances(self) -> list[BalanceSnapshot]:
        now = utcnow()
        return [
            BalanceSnapshot(
                exchange=self.name,
                asset=a,
                free=amt,
                locked=Decimal(0),
                total=amt,
                ts_local=now,
            )
            for a, amt in self._balances.items()
        ]

    async def create_order(self, intent: OrderIntent) -> UnifiedOrderState:
        now = utcnow()
        book = await self.watch_orderbook(intent.symbol)
        fill_price = intent.price
        if fill_price is None:
            fill_price = book.best_ask if intent.side == Side.BUY else book.best_bid

        state = UnifiedOrderState(
            internal_order_id=new_order_id(),
            hedge_group_id=intent.hedge_group_id,
            exchange=self.name,
            symbol=intent.symbol,
            side=intent.side,
            price=intent.price,
            amount=intent.amount,
            filled=intent.amount,
            remaining=Decimal(0),
            avg_fill_price=fill_price,
            status=OrderStatus.FILLED,
            created_at=now,
            updated_at=now,
            exchange_order_id=f"mock-{new_order_id()}",
            client_order_id=intent.client_order_id,
            is_repair=intent.is_repair,
            raw={"mock": True},
        )
        self._orders[state.exchange_order_id or state.internal_order_id] = state
        return state

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> UnifiedOrderState:
        state = self._orders.get(exchange_order_id)
        if not state:
            raise KeyError(exchange_order_id)
        state.status = OrderStatus.CANCELLED
        state.updated_at = utcnow()
        return state

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> UnifiedOrderState:
        state = self._orders.get(exchange_order_id)
        if not state:
            raise KeyError(exchange_order_id)
        return state

    def fee_rate(self, symbol: str, side: str) -> Decimal:
        return self._fee_bps / Decimal("10000")
