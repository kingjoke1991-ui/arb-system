"""In-memory index of live/paper orders keyed by internal id and hedge group."""

from __future__ import annotations

from collections import defaultdict

from app.models.order import UnifiedOrderState


class OrderTracker:
    def __init__(self):
        self._by_internal: dict[str, UnifiedOrderState] = {}
        self._by_hedge: dict[str, list[str]] = defaultdict(list)

    def add(self, order: UnifiedOrderState) -> None:
        self._by_internal[order.internal_order_id] = order
        if order.hedge_group_id:
            self._by_hedge[order.hedge_group_id].append(order.internal_order_id)

    def update(self, order: UnifiedOrderState) -> None:
        self._by_internal[order.internal_order_id] = order

    def get(self, internal_id: str) -> UnifiedOrderState | None:
        return self._by_internal.get(internal_id)

    def by_hedge(self, hedge_id: str) -> list[UnifiedOrderState]:
        return [self._by_internal[i] for i in self._by_hedge.get(hedge_id, []) if i in self._by_internal]

    def all(self) -> list[UnifiedOrderState]:
        return list(self._by_internal.values())

    def recent(self, limit: int = 50) -> list[UnifiedOrderState]:
        # naive: by created_at desc
        return sorted(self._by_internal.values(), key=lambda o: o.created_at, reverse=True)[:limit]
