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
            # live mode requires explicit enable + healthy + non-empty balances
            pass

        return RiskDecision(
            approved=True,
            reason=None,
            detail=None,
            approved_amount=approved_amount,
            approved_notional_quote=approved_amount * mid,
        )
