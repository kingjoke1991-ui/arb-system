"""Simple dependency container. Assembled by ``bootstrap`` and used by the API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.accounts.account_reconciler import AccountReconciler
from app.accounts.balance_manager import BalanceManager
from app.adapters.registry import AdapterRegistry
from app.config.settings import Settings
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
from app.services.alert_service import AlertService
from app.services.config_service import ConfigService
from app.services.metrics_service import Metrics
from app.services.report_service import ReportService
from app.storage.db import Database
from app.storage.repositories.events import EventRepo
from app.storage.repositories.hedges import HedgeRepo
from app.storage.repositories.opportunities import OpportunityRepo
from app.storage.repositories.orders import OrderRepo
from app.strategy.fee_model import FeeModel
from app.strategy.opportunity_scanner import OpportunityScanner
from app.strategy.spread_calculator import SpreadCalculator
from app.strategy.triangular_scanner import TriangularScanner


@dataclass
class Container:
    settings: Settings
    registry: AdapterRegistry
    book_mgr: OrderBookManager
    balance_mgr: BalanceManager
    reconciler: AccountReconciler
    fee_model: FeeModel
    spread_calc: SpreadCalculator
    scanner: OpportunityScanner
    triangular: TriangularScanner
    kill: KillSwitch
    breaker: CircuitBreaker
    health: HealthGuard
    exposure: ExposureManager
    risk: RiskEngine
    paper: PaperFillEngine
    router: OrderRouter
    tracker: OrderTracker
    repair: RepairEngine
    hedge: HedgeCoordinator
    metrics: Metrics
    alerts: AlertService
    config_service: ConfigService
    db: Optional[Database]
    report: Optional[ReportService]
    opp_repo: Optional[OpportunityRepo]
    hedge_repo: Optional[HedgeRepo]
    order_repo: Optional[OrderRepo]
    event_repo: Optional[EventRepo]
