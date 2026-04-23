"""
OrderRouter dispatches a single OrderIntent to the right backend depending on Mode.
"""

from __future__ import annotations

from app.adapters.registry import AdapterRegistry
from app.common.clock import utcnow
from app.common.enums import Mode, OrderStatus
from app.common.ids import new_order_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.execution.paper_fill_engine import PaperFillEngine
from app.models.order import OrderIntent, UnifiedOrderState

log = get_logger("execution.router")


class OrderRouter:
    def __init__(
        self,
        settings: Settings,
        registry: AdapterRegistry,
        paper_engine: PaperFillEngine,
    ):
        self._settings = settings
        self._registry = registry
        self._paper = paper_engine

    async def submit(self, intent: OrderIntent) -> UnifiedOrderState:
        mode = self._settings.mode
        if mode == Mode.DRY_RUN.value:
            now = utcnow()
            log.info(
                "dry_run_order",
                exchange=intent.exchange,
                symbol=intent.symbol,
                side=intent.side.value,
                amount=str(intent.amount),
                price=str(intent.price) if intent.price else None,
            )
            return UnifiedOrderState(
                internal_order_id=new_order_id(),
                hedge_group_id=intent.hedge_group_id,
                exchange=intent.exchange,
                symbol=intent.symbol,
                side=intent.side,
                price=intent.price,
                amount=intent.amount,
                filled=intent.amount,
                remaining=0,
                avg_fill_price=intent.price,
                status=OrderStatus.FILLED,
                created_at=now,
                updated_at=now,
                exchange_order_id=f"dry-{new_order_id()}",
                client_order_id=intent.client_order_id,
                is_repair=intent.is_repair,
                raw={"dry_run": True},
            )

        if mode == Mode.PAPER_TRADE.value:
            return self._paper.simulate(intent)

        # live
        adapter = self._registry.get(intent.exchange)
        return await adapter.create_order(intent)
