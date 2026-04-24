"""
Assembles and wires all components into a Container. Also provides a start/stop
lifecycle for the background loops (market data, balances, scanner).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from app.accounts.account_reconciler import AccountReconciler
from app.accounts.balance_manager import BalanceManager
from app.adapters.registry import build_default_registry
from app.common.clock import utcnow
from app.common.enums import Mode, Severity
from app.common.logging import configure_logging, get_logger
from app.config.settings import Settings, get_settings
from app.execution.hedge_coordinator import HedgeCoordinator
from app.execution.order_router import OrderRouter
from app.execution.order_tracker import OrderTracker
from app.execution.paper_fill_engine import PaperFillEngine
from app.execution.repair_engine import RepairEngine
from app.marketdata.orderbook_manager import OrderBookManager
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.exposure_manager import ExposureManager
from app.risk.health_guard import HealthGuard
from app.risk.kill_switch import KillSwitch
from app.risk.rules import RiskEngine
from app.runtime.dependency_container import Container
from app.services.alert_service import AlertService
from app.services.config_service import ConfigService
from app.services.metrics_service import get_metrics
from app.services.report_service import ReportService
from app.storage.db import Database
from app.storage.repositories.events import EventRepo
from app.storage.repositories.hedges import HedgeRepo
from app.storage.repositories.opportunities import OpportunityRepo
from app.storage.repositories.orders import OrderRepo
from app.strategy.fee_model import FeeModel
from app.strategy.opportunity_scanner import OpportunityScanner
from app.strategy.spread_calculator import SpreadCalculator

log = get_logger("runtime.bootstrap")


_bg_tasks: list[asyncio.Task] = []


def _build_container(settings: Settings) -> Container:
    registry = build_default_registry(settings)
    book_mgr = OrderBookManager(
        max_stale_ms=settings.max_marketdata_staleness_ms,
        poll_interval_ms=settings.scan_interval_ms,
    )
    balance_mgr = BalanceManager(registry, refresh_interval_sec=15)
    reconciler = AccountReconciler(balance_mgr)

    fee_model = FeeModel(registry)
    spread_calc = SpreadCalculator(fee_model)
    kill = KillSwitch(initial=settings.kill_switch)
    breaker = CircuitBreaker(max_consecutive_failures=settings.max_consecutive_failures)
    health = HealthGuard(book_mgr, balance_mgr, settings)
    exposure = ExposureManager()
    risk = RiskEngine(settings, kill, breaker, health, exposure, balance_mgr)

    paper = PaperFillEngine(book_mgr, balance_mgr=balance_mgr)
    tracker = OrderTracker()
    router = OrderRouter(settings, registry, paper)
    repair = RepairEngine(settings, router, tracker, book_mgr)

    hedge = HedgeCoordinator(
        settings,
        router,
        tracker,
        repair,
        exposure,
        breaker=breaker,
        registry=registry,
    )
    scanner = OpportunityScanner(settings, book_mgr, balance_mgr, spread_calc)
    metrics = get_metrics()
    alerts = AlertService(settings)
    config_service = ConfigService(settings)

    return Container(
        settings=settings,
        registry=registry,
        book_mgr=book_mgr,
        balance_mgr=balance_mgr,
        reconciler=reconciler,
        fee_model=fee_model,
        spread_calc=spread_calc,
        scanner=scanner,
        kill=kill,
        breaker=breaker,
        health=health,
        exposure=exposure,
        risk=risk,
        paper=paper,
        router=router,
        tracker=tracker,
        repair=repair,
        hedge=hedge,
        metrics=metrics,
        alerts=alerts,
        config_service=config_service,
        db=None,
        report=None,
        opp_repo=None,
        hedge_repo=None,
        order_repo=None,
        event_repo=None,
    )


async def bootstrap(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    log.info(
        "bootstrap_start",
        env=settings.env,
        mode=settings.mode,
        symbols=settings.enabled_symbol_list,
    )

    c = _build_container(settings)

    # DB optional — if unreachable, we keep running but without persistence.
    db: Database | None = None
    try:
        db = Database(settings.postgres_dsn)
        await db.create_all()
        c.db = db
        c.report = ReportService(db)
        c.opp_repo = OpportunityRepo(db)
        c.hedge_repo = HedgeRepo(db)
        c.order_repo = OrderRepo(db)
        c.event_repo = EventRepo(db)
        log.info("db_ready")
    except Exception as e:  # noqa: BLE001
        log.warning("db_unavailable_running_in_memory", error=str(e))

    # Startup reconciliation: any hedge group the DB still has as active
    # (i.e. not completed/aborted) outlived its parent process. We don't try
    # to auto-resume — instead we mark them with a note, surface them in /events
    # so an operator can decide what to do.
    if c.hedge_repo is not None:
        try:
            active = await c.hedge_repo.active()
            if active:
                log.warning("startup_found_active_hedges", count=len(active))
                for row in active:
                    if c.event_repo is not None:
                        await _safe(
                            c.event_repo.log(
                                event_type="startup_orphan_hedge",
                                severity="warning",
                                component="bootstrap",
                                message=f"hedge_group={row.id} still {row.state} at startup",
                                payload={
                                    "hedge_group_id": row.id,
                                    "symbol": row.symbol,
                                    "state": row.state,
                                    "executed_buy": str(row.executed_buy_amount),
                                    "executed_sell": str(row.executed_sell_amount),
                                    "net_position_base": str(row.net_position_base),
                                },
                            )
                        )
        except Exception as e:  # noqa: BLE001
            log.warning("startup_reconcile_error", error=str(e))

    # Virtual balances for paper-trade so the risk engine has something to gate on.
    if settings.mode in (Mode.PAPER_TRADE.value, Mode.DRY_RUN.value):
        for ex in c.registry.names():
            c.balance_mgr.set_virtual_balance(ex, "USDT", Decimal("10000"))
            for s in settings.enabled_symbol_list:
                base = s.split("/")[0]
                c.balance_mgr.set_virtual_balance(ex, base, Decimal("1"))

    # Connect adapters (non-fatal if fails)
    try:
        await c.registry.connect_all()
    except Exception as e:  # noqa: BLE001
        log.warning("adapter_connect_issue", error=str(e))

    # Background tasks
    _bg_tasks.append(
        asyncio.create_task(
            c.book_mgr.run(c.registry.all(), settings.enabled_symbol_list),
            name="orderbook_loop",
        )
    )
    _bg_tasks.append(asyncio.create_task(c.balance_mgr.run(), name="balance_loop"))
    _bg_tasks.append(asyncio.create_task(_scanner_loop(c), name="scanner_loop"))

    return c


async def _scanner_loop(c: Container) -> None:
    settings = c.settings
    c.scanner.start()
    interval = settings.scan_interval_ms / 1000.0
    from app.common.enums import Mode as _M

    while c.scanner.is_running():
        # Respect the operator-set pause flag without dropping out of the loop
        # (so unpausing is instantaneous).
        if settings.paused:
            await asyncio.sleep(interval)
            continue
        # Strategy-level master switch. We only run cross_exchange_spot today;
        # when its toggle is off the loop idles (but still obeys pause/mode).
        if not getattr(settings, "strategy_cross_exchange_spot_enabled", True):
            await asyncio.sleep(interval)
            continue
        try:
            opps = await c.scanner.scan_once(c.registry.names())
            for opp in opps:
                c.metrics.opp_detected_total.labels(symbol=opp.symbol).inc()
                c.metrics.net_edge_bps_hist.labels(symbol=opp.symbol).observe(float(opp.net_edge_bps))
                decision = c.risk.evaluate(opp)
                if decision.approved:
                    opp.decision = "accepted"
                    c.metrics.opp_accepted_total.labels(symbol=opp.symbol).inc()
                    if c.opp_repo:
                        await _safe(c.opp_repo.save(opp, decision="accepted"))
                    # In dry-run we still go through execute so the full audit trail is produced.
                    if settings.mode in (_M.DRY_RUN.value, _M.PAPER_TRADE.value, _M.LIVE.value):
                        group = await c.hedge.execute(opp, decision.approved_amount)
                        if c.hedge_repo:
                            await _safe(c.hedge_repo.upsert(group))
                        c.risk.set_cooldown(opp.symbol)
                else:
                    opp.decision = "rejected"
                    opp.decision_reason = decision.reason.value if decision.reason else "unknown"
                    c.metrics.opp_rejected_total.labels(symbol=opp.symbol, reason=opp.decision_reason).inc()
                    c.metrics.risk_reject_total.labels(reason=opp.decision_reason).inc()
                    if c.opp_repo:
                        await _safe(c.opp_repo.save(opp, decision="rejected", reason=opp.decision_reason))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("scanner_iteration_error", error=str(e))
        await asyncio.sleep(interval)


async def _safe(coro) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        log.warning("persistence_error", error=str(e))


async def teardown(c: Container) -> None:
    c.scanner.stop()
    await c.book_mgr.stop()
    await c.balance_mgr.stop()
    for t in _bg_tasks:
        t.cancel()
    for t in _bg_tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _bg_tasks.clear()
    await c.registry.close_all()
    if c.db:
        await c.db.close()
    log.info("teardown_complete")
