"""
Central risk engine — applies all rules to a candidate opportunity and returns
an accept/reject decision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from app.accounts.balance_manager import BalanceManager
from app.common.clock import utcnow
from app.common.enums import Mode, RejectReason
from app.common.logging import get_logger
from app.config.settings import Settings
from app.models.opportunity import ArbitrageOpportunity
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.exposure_manager import ExposureManager
from app.risk.health_guard import HealthGuard
from app.risk.kill_switch import KillSwitch

log = get_logger("risk.engine")


@dataclass
class RiskDecision:
    approved: bool
    reason: RejectReason | None
    detail: str | None
    approved_amount: Decimal = Decimal(0)
    approved_notional_quote: Decimal = Decimal(0)


class RiskEngine:
    def __init__(
        self,
        settings: Settings,
        kill: KillSwitch,
        breaker: CircuitBreaker,
        health: HealthGuard,
        exposure: ExposureManager,
        balances: BalanceManager,
    ):
        self._settings = settings
        self._kill = kill
        self._breaker = breaker
        self._health = health
        self._exposure = exposure
        self._balances = balances
        # symbol -> cooldown_until_epoch_sec
        self._cooldown: dict[str, float] = {}
        # Issue 2 — rolling 24h loss tracker. Reset whenever the wall clock
        # crosses the next UTC day boundary. Live mode gates new hedges
        # when accumulated loss exceeds ``max_daily_loss_quote``.
        self._daily_loss_quote: Decimal = Decimal(0)
        self._daily_loss_day: str | None = None

    def _today_key(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def record_realized_pnl(self, pnl: Decimal | None) -> None:
        """Called by the bootstrap after ``HedgeCoordinator.execute`` so the
        live-mode daily-loss gate stays accurate without touching the DB.
        """
        if pnl is None:
            return
        today = self._today_key()
        if self._daily_loss_day != today:
            self._daily_loss_day = today
            self._daily_loss_quote = Decimal(0)
        if pnl < 0:
            self._daily_loss_quote += -pnl

    def daily_loss_quote(self) -> Decimal:
        today = self._today_key()
        if self._daily_loss_day != today:
            return Decimal(0)
        return self._daily_loss_quote

    def set_cooldown(self, symbol: str, seconds: int | None = None) -> None:
        s = seconds if seconds is not None else self._settings.cooldown_seconds
        self._cooldown[symbol.upper()] = time.time() + s

    def _in_cooldown(self, symbol: str) -> bool:
        until = self._cooldown.get(symbol.upper(), 0.0)
        return time.time() < until

    def evaluate(self, opp: ArbitrageOpportunity) -> RiskDecision:
        mode = self._settings.mode

        if self._kill.is_on():
            return RiskDecision(False, RejectReason.KILL_SWITCH, self._kill.reason)

        if self._breaker.tripped:
            return RiskDecision(False, RejectReason.CIRCUIT_BREAKER, self._breaker.reason)

        if opp.symbol.upper() not in [s.upper() for s in self._settings.enabled_symbol_list]:
            return RiskDecision(False, RejectReason.SYMBOL_NOT_WHITELISTED, opp.symbol)

        if self._in_cooldown(opp.symbol):
            return RiskDecision(False, RejectReason.COOLDOWN_ACTIVE, opp.symbol)

        if not self._health.all_ok([opp.buy_exchange, opp.sell_exchange]):
            return RiskDecision(
                False,
                RejectReason.EXCHANGE_UNHEALTHY,
                f"{opp.buy_exchange}/{opp.sell_exchange} unhealthy",
            )

        # Issue 5 — per-trade book age cap. Tighter than the health-level
        # staleness threshold so a 700ms-old book gets rejected while
        # the feed itself is still considered healthy.
        max_age = getattr(self._settings, "max_book_age_ms_for_trade", None)
        if max_age is not None:
            buy_age = opp.buy_book_age_ms
            sell_age = opp.sell_book_age_ms
            # Treat None defensively: opportunities created without book-age
            # context (older code paths, tests) skip this gate.
            if buy_age is not None and buy_age > max_age:
                return RiskDecision(False, RejectReason.BOOK_TOO_OLD, f"buy_age={buy_age}ms > {max_age}ms")
            if sell_age is not None and sell_age > max_age:
                return RiskDecision(False, RejectReason.BOOK_TOO_OLD, f"sell_age={sell_age}ms > {max_age}ms")

        if opp.net_edge_bps < self._settings.min_net_edge_bps:
            return RiskDecision(False, RejectReason.BELOW_MIN_EDGE, str(opp.net_edge_bps))

        if opp.expected_profit_quote < self._settings.min_profit_quote:
            return RiskDecision(False, RejectReason.BELOW_MIN_PROFIT, str(opp.expected_profit_quote))

        # Translate whatever exchange gave us into an actual size honoring:
        # - max_notional_per_trade
        # - exchange exposure limit minus in-flight
        # - available balances (only enforced when mode != dry-run)
        notional_cap_per_trade = self._settings.max_notional_per_trade
        mid = opp.buy_price or Decimal(1)
        if mid == 0:
            mid = Decimal(1)

        size_from_cap = notional_cap_per_trade / mid
        size_from_exposure_buy = (
            max(
                Decimal(0),
                self._settings.max_exposure_per_exchange - self._exposure.in_flight(opp.buy_exchange),
            )
            / mid
        )
        size_from_exposure_sell = (
            max(
                Decimal(0),
                self._settings.max_exposure_per_exchange - self._exposure.in_flight(opp.sell_exchange),
            )
            / mid
        )

        candidates = [
            opp.max_tradable_size,
            size_from_cap,
            size_from_exposure_buy,
            size_from_exposure_sell,
        ]

        if mode != Mode.DRY_RUN.value:
            base, quote = opp.symbol.upper().split("/")
            quote_avail = self._balances.free(opp.buy_exchange, quote)
            base_avail = self._balances.free(opp.sell_exchange, base)
            size_from_quote = (quote_avail / mid) if mid > 0 else Decimal(0)
            candidates.extend([size_from_quote, base_avail])

        approved_amount = min([c for c in candidates if c is not None])

        if approved_amount * mid < self._settings.min_order_size_quote:
            return RiskDecision(False, RejectReason.BELOW_MIN_SIZE, str(approved_amount))

        if self._exposure.open_count() >= self._settings.max_total_open_hedges:
            return RiskDecision(False, RejectReason.TOO_MANY_OPEN_HEDGES, str(self._exposure.open_count()))

        if mode == Mode.LIVE.value:
            # Issue 2 — live-mode hard gates. The previous ``pass`` here
            # meant a streak of losing trades would never auto-stop the
            # system as long as both legs technically filled.
            max_daily = getattr(self._settings, "max_daily_loss_quote", None)
            if max_daily is not None and self.daily_loss_quote() >= max_daily:
                return RiskDecision(
                    False,
                    RejectReason.DAILY_LOSS_LIMIT,
                    f"daily_loss={self.daily_loss_quote()} >= {max_daily}",
                )
            max_consec = getattr(self._settings, "max_consecutive_losing_trades", None)
            if max_consec is not None and getattr(self._breaker, "consecutive_losses", 0) >= max_consec:
                return RiskDecision(
                    False,
                    RejectReason.CONSECUTIVE_LOSS_LIMIT,
                    f"consecutive_losses={self._breaker.consecutive_losses} >= {max_consec}",
                )

        return RiskDecision(
            approved=True,
            reason=None,
            detail=None,
            approved_amount=approved_amount,
            approved_notional_quote=approved_amount * mid,
        )
