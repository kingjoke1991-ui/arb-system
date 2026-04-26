"""
Repair engine: if a hedge group finishes with a net_position_base != 0, submit
a corrective trade. Strictly tagged as is_repair.

Issue 1: After every repair fill, fold the corrective trade's signed cost,
proceeds, and fees back into ``group.realized_pnl_quote`` and
``group.repair_cost_quote``. Without this, recorded PnL silently excludes
repair losses.

Issue 3: Before submitting, estimate the worst-case PnL impact of the
repair using the current book's VWAP. If the projected post-repair PnL is
worse than ``-max_repair_loss_quote``, refuse to repair and leave the
group in FAILED_NEEDS_REPAIR for operator review.
"""

from __future__ import annotations

from decimal import Decimal

from app.common.enums import HedgeState, OrderType, Side
from app.common.ids import new_client_order_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.execution.execution_policy import in_band_depth
from app.execution.order_router import OrderRouter
from app.execution.order_tracker import OrderTracker
from app.execution.state_machine import HedgeStateMachine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.hedge import HedgeGroupState
from app.models.order import OrderIntent
from app.strategy.slippage_model import vwap_buy, vwap_sell

log = get_logger("execution.repair")


class RepairEngine:
    def __init__(
        self,
        settings: Settings,
        router: OrderRouter,
        tracker: OrderTracker,
        book_mgr: OrderBookManager,
    ):
        self._settings = settings
        self._router = router
        self._tracker = tracker
        self._books = book_mgr

    async def repair(self, group: HedgeGroupState) -> HedgeGroupState:
        net = group.net_position_base
        if net == 0:
            group.state = HedgeState.COMPLETED
            return group

        if group.repair_attempts >= self._settings.max_repair_attempts:
            group.notes.append(f"repair attempts exhausted ({group.repair_attempts})")
            group.failure_reason = group.failure_reason or "repair_attempts_exhausted"
            group.state = HedgeState.ABORTED
            return group

        if net > 0:
            # net long base -> sell on sell_exchange (cheaper venue for us to offload)
            primary_ex = group.sell_exchange
            primary_side = Side.SELL
            # Fallback: close on buy_exchange (the leg that filled). Same
            # base direction (sell) but on the opposite venue, which by
            # construction has demonstrated enough depth to absorb the
            # original buy fill — therefore should also absorb a same-size
            # sell.
            fallback_ex = group.buy_exchange
            fallback_side = Side.SELL
            amount = net
        else:
            # net short base -> buy on buy_exchange
            primary_ex = group.buy_exchange
            primary_side = Side.BUY
            fallback_ex = group.sell_exchange
            fallback_side = Side.BUY
            amount = -net

        # Smarter venue selection: when the original failed leg's book
        # is too thin to absorb the residual position, pivot to the
        # *filled* leg and reverse there instead. This trades the
        # original spread (round-trip cost ~ 2 * buffer) for avoiding
        # an unhedged directional position. Without this, repair
        # repeatedly retargets the same broken venue and can leave the
        # group in FAILED_NEEDS_REPAIR for hours.
        primary_book = self._books.get(primary_ex, group.symbol)
        fallback_book = self._books.get(fallback_ex, group.symbol)
        primary_depth = in_band_depth(primary_book, primary_side, self._settings.ioc_price_buffer_bps)
        fallback_depth = in_band_depth(fallback_book, fallback_side, self._settings.ioc_price_buffer_bps)
        if primary_depth < amount and fallback_book is not None and fallback_depth > primary_depth:
            log.warning(
                "repair_pivot_to_filled_leg",
                hedge_group_id=group.hedge_group_id,
                primary_exchange=primary_ex,
                primary_depth=str(primary_depth),
                fallback_exchange=fallback_ex,
                fallback_depth=str(fallback_depth),
                amount=str(amount),
            )
            exch = fallback_ex
            side = fallback_side
            book = fallback_book
            group.notes.append(
                f"repair_pivot_to_filled_leg: {primary_ex} depth={primary_depth} "
                f"-> {fallback_ex} depth={fallback_depth} amount={amount}"
            )
        else:
            exch = primary_ex
            side = primary_side
            book = primary_book

        # ---- Issue 3: estimate worst-case repair loss before submitting ----
        max_loss = getattr(self._settings, "max_repair_loss_quote", None)
        if max_loss is not None and book is not None:
            # Use VWAP at the residual size as the realistic fill price.
            est_vwap, est_filled, _ = vwap_sell(book, amount) if side == Side.SELL else vwap_buy(book, amount)
            if est_filled > 0:
                # Apply the same IOC buffer the live order will use, in the
                # adverse direction.
                buf = self._settings.ioc_price_buffer_bps / Decimal("10000")
                if side == Side.SELL:
                    est_vwap = est_vwap * (Decimal(1) - buf)
                else:
                    est_vwap = est_vwap * (Decimal(1) + buf)
                # Fee estimate: reuse buy_fee_bps / sell_fee_bps from group
                # opportunity isn't available here; conservatively use
                # ioc_price_buffer_bps as an extra cushion already applied.
                signed_cost = est_vwap * est_filled
                projected_delta = signed_cost if side == Side.SELL else -signed_cost
                current = group.realized_pnl_quote or Decimal(0)
                projected_pnl = current + projected_delta
                if projected_pnl < -max_loss:
                    group.notes.append(
                        f"repair_skipped_max_loss projected_pnl={projected_pnl} threshold={-max_loss}"
                    )
                    group.failure_reason = "repair_skipped_max_loss"
                    group.state = HedgeState.FAILED_NEEDS_REPAIR
                    log.warning(
                        "repair_skipped_max_loss",
                        hedge_group_id=group.hedge_group_id,
                        projected_pnl=str(projected_pnl),
                        threshold=str(-max_loss),
                    )
                    return group

        HedgeStateMachine.assert_transition(group.state, HedgeState.REPAIRING)
        group.state = HedgeState.REPAIRING
        group.repair_attempts += 1

        ref = (book.best_bid if side == Side.SELL else book.best_ask) if book else None
        # Apply an aggressive price buffer so the IOC repair order fills even
        # if the book moves slightly between read and execution.
        if ref is not None:
            buffer = self._settings.ioc_price_buffer_bps / Decimal("10000")
            if side == Side.SELL:
                ref = ref * (Decimal(1) - buffer)
            else:
                ref = ref * (Decimal(1) + buffer)
        intent = OrderIntent(
            hedge_group_id=group.hedge_group_id,
            exchange=exch,
            symbol=group.symbol,
            side=side,
            order_type=OrderType.IOC_LIMIT,
            price=ref,
            amount=amount,
            client_order_id=new_client_order_id(group.hedge_group_id, "r"),
            is_repair=True,
        )
        state = await self._router.submit(intent)
        self._tracker.add(state)
        log.info(
            "repair_submitted",
            hedge_group_id=group.hedge_group_id,
            side=side.value,
            amount=str(amount),
            status=state.status.value,
            exchange=exch,
        )

        filled = state.filled
        if side == Side.BUY:
            group.executed_buy_amount += filled
        else:
            group.executed_sell_amount += filled
        group.net_position_base = group.executed_buy_amount - group.executed_sell_amount

        # ---- Issue 1: fold this repair fill into realized PnL ----
        if filled > 0 and state.avg_fill_price is not None:
            cost = state.avg_fill_price * filled
            fee = state.fee_amount or Decimal(0)
            # SELL credits quote, BUY debits quote.
            delta = (cost - fee) if side == Side.SELL else (-cost - fee)
            group.repair_cost_quote += delta
            group.actual_fee_quote += fee
            if group.realized_pnl_quote is None:
                group.realized_pnl_quote = delta
            else:
                group.realized_pnl_quote += delta

        if abs(group.net_position_base) <= Decimal("0.00000001"):
            HedgeStateMachine.assert_transition(group.state, HedgeState.COMPLETED)
            group.state = HedgeState.COMPLETED
        else:
            HedgeStateMachine.assert_transition(group.state, HedgeState.FAILED_NEEDS_REPAIR)
            group.state = HedgeState.FAILED_NEEDS_REPAIR
        return group
