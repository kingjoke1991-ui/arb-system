"""
Net-edge calculation. Ties together fee + slippage + safety buffer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.orderbook import OrderBookSnapshot
from app.strategy.fee_model import FeeModel
from app.strategy.slippage_model import vwap_buy, vwap_sell


@dataclass
class LegEstimate:
    exchange: str
    symbol: str
    side: str  # "buy" or "sell"
    effective_price: Decimal
    fillable_size: Decimal
    slippage_bps: Decimal
    fee_bps: Decimal


@dataclass
class SpreadEstimate:
    buy_leg: LegEstimate
    sell_leg: LegEstimate
    gross_spread_bps: Decimal
    net_edge_bps: Decimal
    slippage_bps_total: Decimal
    fee_bps_total: Decimal
    buffer_bps: Decimal
    max_tradable_base: Decimal
    expected_profit_quote: Decimal


class SpreadCalculator:
    def __init__(
        self,
        fee_model: FeeModel,
        buffer_bps: Decimal = Decimal("2"),
    ):
        self._fees = fee_model
        self._buffer_bps = buffer_bps

    def evaluate_direction(
        self,
        symbol: str,
        buy_book: OrderBookSnapshot,
        sell_book: OrderBookSnapshot,
        probe_size_base: Decimal,
    ) -> SpreadEstimate | None:
        """
        Returns an estimate for buying at ``buy_book.exchange`` and selling at ``sell_book.exchange``.
        ``probe_size_base`` is a candidate base-asset size to evaluate.
        Returns ``None`` when the books are empty.
        """
        if not buy_book.asks or not sell_book.bids:
            return None

        buy_vwap, buy_fill, buy_slip_bps = vwap_buy(buy_book, probe_size_base)
        sell_vwap, sell_fill, sell_slip_bps = vwap_sell(sell_book, probe_size_base)
        if buy_fill == 0 or sell_fill == 0 or buy_vwap == 0:
            return None

        fillable = min(buy_fill, sell_fill)

        buy_fee_bps = self._fees.taker_bps(buy_book.exchange, symbol, "buy")
        sell_fee_bps = self._fees.taker_bps(sell_book.exchange, symbol, "sell")

        # net_edge_bps (relative to buy_vwap):
        # net_sell = sell_vwap * (1 - sell_fee)
        # net_buy  = buy_vwap  * (1 + buy_fee)
        one = Decimal(1)
        fee_factor_buy = buy_fee_bps / Decimal("10000")
        fee_factor_sell = sell_fee_bps / Decimal("10000")
        net_sell = sell_vwap * (one - fee_factor_sell)
        net_buy = buy_vwap * (one + fee_factor_buy)
        raw_edge_bps = (net_sell - net_buy) / buy_vwap * Decimal("10000")
        gross_bps = (sell_vwap - buy_vwap) / buy_vwap * Decimal("10000")
        # Note: slippage is already baked into the vwap prices; we only subtract the
        # explicit safety buffer here.
        net_edge_bps = raw_edge_bps - self._buffer_bps

        expected_profit_quote = (net_sell - net_buy) * fillable - (
            self._buffer_bps / Decimal("10000")
        ) * buy_vwap * fillable

        return SpreadEstimate(
            buy_leg=LegEstimate(
                exchange=buy_book.exchange,
                symbol=symbol,
                side="buy",
                effective_price=buy_vwap,
                fillable_size=buy_fill,
                slippage_bps=buy_slip_bps,
                fee_bps=buy_fee_bps,
            ),
            sell_leg=LegEstimate(
                exchange=sell_book.exchange,
                symbol=symbol,
                side="sell",
                effective_price=sell_vwap,
                fillable_size=sell_fill,
                slippage_bps=sell_slip_bps,
                fee_bps=sell_fee_bps,
            ),
            gross_spread_bps=gross_bps,
            net_edge_bps=net_edge_bps,
            slippage_bps_total=buy_slip_bps + sell_slip_bps,
            fee_bps_total=buy_fee_bps + sell_fee_bps,
            buffer_bps=self._buffer_bps,
            max_tradable_base=fillable,
            expected_profit_quote=expected_profit_quote,
        )
