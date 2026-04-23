"""
Generic CCXT-based adapter. We use plain ``ccxt`` (sync lib wrapped in asyncio.to_thread)
by default so we don't require ``ccxt.pro`` to run. WebSocket streaming can be added by
subclassing and overriding ``watch_orderbook``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.adapters.base import ExchangeAdapter
from app.common.clock import utcnow
from app.common.enums import OrderStatus, OrderType, Side
from app.common.exceptions import AuthError, PermanentError, RateLimitError, TransientError
from app.common.ids import new_order_id
from app.common.logging import get_logger
from app.models.balance import BalanceSnapshot
from app.models.order import OrderIntent, UnifiedOrderState
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot

log = get_logger("adapter.ccxt")


def _d(x: Any) -> Decimal:
    if x is None:
        return Decimal(0)
    return Decimal(str(x))


_STATUS_MAP = {
    "open": OrderStatus.SUBMITTED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
}


class CcxtExchangeAdapter(ExchangeAdapter):
    """
    Wraps a ``ccxt`` sync exchange instance with asyncio.to_thread.
    """

    def __init__(
        self,
        name: str,
        ccxt_client: Any,
        default_fee_bps: Decimal = Decimal("10"),
    ):
        self.name = name
        self._client = ccxt_client
        self._default_fee_bps = default_fee_bps
        self._connected = False

    @property
    def is_configured(self) -> bool:
        apikey = getattr(self._client, "apiKey", "") or ""
        return bool(apikey)

    async def connect(self) -> None:
        try:
            await asyncio.to_thread(self._client.load_markets)
            self._connected = True
            log.info("exchange_connected", exchange=self.name)
        except Exception as e:
            log.warning("exchange_connect_failed", exchange=self.name, error=str(e))
            # We don't raise: scanner will mark this exchange unhealthy instead.

    async def close(self) -> None:
        try:
            close = getattr(self._client, "close", None)
            if callable(close):
                res = close()
                if asyncio.iscoroutine(res):
                    await res
        except Exception:  # noqa: BLE001
            pass
        self._connected = False

    async def watch_orderbook(self, symbol: str) -> OrderBookSnapshot:
        """One-shot REST fetch. Long-running watchers poll this in a loop."""
        try:
            ob = await asyncio.to_thread(self._client.fetch_order_book, symbol, 25)
        except Exception as e:  # noqa: BLE001
            cls_name = type(e).__name__
            if "RateLimit" in cls_name:
                raise RateLimitError(str(e)) from e
            if "Auth" in cls_name:
                raise AuthError(str(e)) from e
            raise TransientError(f"orderbook fetch failed: {e}") from e

        now = utcnow()
        ts_ex = None
        if ob.get("timestamp"):
            ts_ex = datetime.fromtimestamp(ob["timestamp"] / 1000, tz=timezone.utc)
        latency_ms = None
        if ts_ex is not None:
            latency_ms = max(0, int((now - ts_ex).total_seconds() * 1000))

        bids = [OrderBookLevel(_d(p), _d(s)) for p, s in (ob.get("bids") or [])]
        asks = [OrderBookLevel(_d(p), _d(s)) for p, s in (ob.get("asks") or [])]
        return OrderBookSnapshot(
            exchange=self.name,
            symbol=symbol,
            bids=bids,
            asks=asks,
            ts_local=now,
            ts_exchange=ts_ex,
            latency_ms=latency_ms,
        )

    async def fetch_balances(self) -> list[BalanceSnapshot]:
        if not self.is_configured:
            return []
        try:
            bals = await asyncio.to_thread(self._client.fetch_balance)
        except Exception as e:  # noqa: BLE001
            raise TransientError(f"balance fetch failed: {e}") from e
        out: list[BalanceSnapshot] = []
        now = utcnow()
        total_map = bals.get("total", {}) or {}
        free_map = bals.get("free", {}) or {}
        used_map = bals.get("used", {}) or {}
        for asset in total_map:
            total = _d(total_map.get(asset))
            if total == 0:
                continue
            out.append(
                BalanceSnapshot(
                    exchange=self.name,
                    asset=asset,
                    free=_d(free_map.get(asset)),
                    locked=_d(used_map.get(asset)),
                    total=total,
                    ts_local=now,
                )
            )
        return out

    def _ccxt_order_type(self, order_type: OrderType) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {}
        if order_type == OrderType.MARKET:
            return "market", params
        if order_type == OrderType.IOC_LIMIT:
            params["timeInForce"] = "IOC"
            return "limit", params
        if order_type == OrderType.FOK_LIMIT:
            params["timeInForce"] = "FOK"
            return "limit", params
        return "limit", params

    async def create_order(self, intent: OrderIntent) -> UnifiedOrderState:
        t, params = self._ccxt_order_type(intent.order_type)
        if intent.client_order_id:
            params["clientOrderId"] = intent.client_order_id
        try:
            raw = await asyncio.to_thread(
                self._client.create_order,
                intent.symbol,
                t,
                intent.side.value,
                float(intent.amount),
                float(intent.price) if intent.price is not None else None,
                params,
            )
        except Exception as e:  # noqa: BLE001
            cls_name = type(e).__name__
            if "RateLimit" in cls_name:
                raise RateLimitError(str(e)) from e
            if "Auth" in cls_name or "Permission" in cls_name:
                raise AuthError(str(e)) from e
            if "Insufficient" in cls_name or "InvalidOrder" in cls_name:
                raise PermanentError(str(e)) from e
            raise TransientError(str(e)) from e

        return self._normalize_order(raw, intent)

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> UnifiedOrderState:
        try:
            raw = await asyncio.to_thread(self._client.cancel_order, exchange_order_id, symbol)
        except Exception as e:  # noqa: BLE001
            raise TransientError(f"cancel_order failed: {e}") from e
        return self._normalize_order(raw, intent=None, symbol=symbol)

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> UnifiedOrderState:
        try:
            raw = await asyncio.to_thread(self._client.fetch_order, exchange_order_id, symbol)
        except Exception as e:  # noqa: BLE001
            raise TransientError(f"fetch_order failed: {e}") from e
        return self._normalize_order(raw, intent=None, symbol=symbol)

    def fee_rate(self, symbol: str, side: str) -> Decimal:
        """
        Prefer ccxt's ``markets[symbol]['taker']`` if present.
        """
        try:
            m = self._client.markets.get(symbol) if getattr(self._client, "markets", None) else None
            if m and "taker" in m and m["taker"] is not None:
                return _d(m["taker"])
        except Exception:  # noqa: BLE001
            pass
        return self._default_fee_bps / Decimal("10000")

    # --- helpers ---
    def _normalize_order(
        self,
        raw: dict[str, Any],
        intent: OrderIntent | None,
        symbol: str | None = None,
    ) -> UnifiedOrderState:
        status_s = (raw.get("status") or "").lower()
        status = _STATUS_MAP.get(status_s, OrderStatus.UNKNOWN)
        filled = _d(raw.get("filled"))
        amount = _d(raw.get("amount") or (intent.amount if intent else 0))
        remaining = _d(raw.get("remaining") or max(amount - filled, Decimal(0)))
        if status == OrderStatus.SUBMITTED and filled > 0 and remaining > 0:
            status = OrderStatus.PARTIALLY_FILLED

        fee = raw.get("fee") or {}
        fee_amt = _d(fee.get("cost"))
        fee_asset = fee.get("currency")

        now = utcnow()
        return UnifiedOrderState(
            internal_order_id=new_order_id(),
            hedge_group_id=intent.hedge_group_id if intent else "",
            exchange=self.name,
            exchange_order_id=str(raw.get("id")) if raw.get("id") is not None else None,
            client_order_id=raw.get("clientOrderId"),
            symbol=raw.get("symbol") or symbol or (intent.symbol if intent else ""),
            side=Side((raw.get("side") or (intent.side.value if intent else "buy")).lower()),
            price=_d(raw.get("price")) if raw.get("price") else (intent.price if intent else None),
            amount=amount,
            filled=filled,
            remaining=remaining,
            avg_fill_price=_d(raw.get("average")) if raw.get("average") else None,
            status=status,
            fee_amount=fee_amt if fee_amt else None,
            fee_asset=fee_asset,
            is_repair=intent.is_repair if intent else False,
            created_at=now,
            updated_at=now,
            raw=raw,
        )
