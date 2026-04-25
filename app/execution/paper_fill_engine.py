"""
Paper-trade fill engine. Walks the current book and decides fill amount,
allowing simulated partial fills when book depth < requested amount.
Credits/debits virtual balances so the paper-trade account state is
actually updated and risk controls (exposure, insufficient balance) can
be exercised end-to-end.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.accounts.balance_manager import BalanceManager
from app.common.clock import utcnow
from app.common.enums import OrderStatus, OrderType, Side
from app.common.ids import new_order_id
from app.common.logging import get_logger
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.order import OrderIntent, UnifiedOrderState

if TYPE_CHECKING:
    from app.strategy.fee_model import FeeModel

log = get_logger("execution.paper")


@dataclass
class PaperFillConfig:
    # probability that the buy leg fills only partially (for partial-fill tests)
    partial_fill_probability: float = 0.0
    partial_fill_ratio: float = 0.6
    fee_bps: Decimal = Decimal("10")


class PaperFillEngine:
    def __init__(
        self,
        book_mgr: OrderBookManager,
        cfg: PaperFillConfig | None = None,
        balance_mgr: BalanceManager | None = None,
        fee_model: FeeModel | None = None,
    ):
        self._books = book_mgr
        self._cfg = cfg or PaperFillConfig()
        self._balances = balance_mgr
        self._fee_model = fee_model
        self._rng = random.Random(42)

    def simulate(self, intent: OrderIntent) -> UnifiedOrderState:
        book = self._books.get(intent.exchange, intent.symbol)
        now = utcnow()
        if book is None:
            return UnifiedOrderState(
                internal_order_id=new_order_id(),
                hedge_group_id=intent.hedge_group_id,
                exchange=intent.exchange,
                symbol=intent.symbol,
                side=intent.side,
                price=intent.price,
                amount=intent.amount,
                filled=Decimal(0),
                remaining=intent.amount,
                avg_fill_price=None,
                status=OrderStatus.REJECTED,
                created_at=now,
                updated_at=now,
                is_repair=intent.is_repair,
            )

        levels = book.asks if intent.side == Side.BUY else book.bids
        remaining = intent.amount
        filled = Decimal(0)
        cost = Decimal(0)
        # Issue 4 — respect the IOC/limit price. Pre-fix this engine walked
        # every level regardless of ``intent.price``, so a fill that would
        # have been rejected on a real venue (book moved past the protected
        # limit) silently consumed the worst levels of the book at unrealistic
        # prices.
        is_limit = intent.order_type in (
            OrderType.LIMIT,
            OrderType.IOC_LIMIT,
            OrderType.FOK_LIMIT,
        )
        limit_price = intent.price if is_limit else None
        for lvl in levels:
            if limit_price is not None:
                if intent.side == Side.BUY and lvl.price > limit_price:
                    break
                if intent.side == Side.SELL and lvl.price < limit_price:
                    break
            take = min(remaining, lvl.size)
            if take <= 0:
                break
            cost += take * lvl.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break

        # Optional partial-fill dice
        if (
            not intent.is_repair
            and filled > 0
            and self._cfg.partial_fill_probability > 0.0
            and self._rng.random() < self._cfg.partial_fill_probability
        ):
            ratio = Decimal(str(self._cfg.partial_fill_ratio))
            new_filled = filled * ratio
            if new_filled > 0:
                # scale cost accordingly
                avg = cost / filled
                filled = new_filled
                cost = new_filled * avg
                remaining = intent.amount - filled

        avg_price = cost / filled if filled > 0 else None
        # Use exchange-specific fee from FeeModel when available; fall back
        # to the static config default.
        if filled > 0:
            fee_bps = self._cfg.fee_bps
            if self._fee_model is not None:
                side_str = "buy" if intent.side == Side.BUY else "sell"
                fee_bps = self._fee_model.taker_bps(intent.exchange, intent.symbol, side_str)
            fee_amount = cost * fee_bps / Decimal("10000")
        else:
            fee_amount = None
        status = (
            OrderStatus.FILLED
            if remaining <= Decimal("0.0000000001")
            else (OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.CANCELLED)
        )

        # ---------- Virtual balance bookkeeping ----------
        # BUY  leg on exchange X: base += filled, quote -= cost + fee
        # SELL leg on exchange X: base -= filled, quote += proceeds - fee
        if self._balances is not None and filled > 0 and "/" in intent.symbol:
            base, quote = intent.symbol.upper().split("/", 1)
            fee = fee_amount or Decimal(0)
            if intent.side == Side.BUY:
                self._balances.adjust_virtual(intent.exchange, base, filled)
                self._balances.adjust_virtual(intent.exchange, quote, -(cost + fee))
            else:
                self._balances.adjust_virtual(intent.exchange, base, -filled)
                self._balances.adjust_virtual(intent.exchange, quote, cost - fee)

        return UnifiedOrderState(
            internal_order_id=new_order_id(),
            hedge_group_id=intent.hedge_group_id,
            exchange=intent.exchange,
            symbol=intent.symbol,
            side=intent.side,
            price=intent.price,
            amount=intent.amount,
            filled=filled,
            remaining=max(Decimal(0), remaining),
            avg_fill_price=avg_price,
            status=status,
            created_at=now,
            updated_at=now,
            exchange_order_id=f"paper-{new_order_id()}",
            client_order_id=intent.client_order_id,
            fee_amount=fee_amount,
            fee_asset=intent.symbol.split("/")[1],
            is_repair=intent.is_repair,
            raw={"paper": True},
        )
