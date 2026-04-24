"""Funding-rate arbitrage scanner (detect-only V1).

A perpetual swap's funding rate transfers a small payment between longs
and shorts every funding period (8h on Binance USDⓈ-M, 8h on OKX SWAP).
If the funding rate is positive, longs pay shorts; if negative, shorts
pay longs.

The classic "cash-and-carry" funding-rate arb:

* Funding rate > 0 and large enough:
    - SHORT the perp (collect funding from longs)
    - LONG the spot  (hedge the price risk)
    Net PnL per period = funding_rate × notional − fees − spot–perp basis
* Funding rate < 0:
    - LONG the perp, SHORT the spot (requires borrow or margin) — we
      skip this direction unless the operator opts in because borrow is
      more complex than we want at V1.

Our scanner reads the live funding rate via ccxt's
``fetch_funding_rate`` on the configured exchange (binance USDⓈ-M by
default) and emits a ``FundingRateOpportunity`` whenever the annualised
rate exceeds ``funding_rate_min_apr_bps``. We compare against the SPOT
top-of-book ask/bid from the existing ``OrderBookManager`` so the UI
can show a side-by-side price plus an estimated per-period profit.

**Execution is deliberately out of scope.** Actually opening the legs
requires:
  1. A perp adapter that can place orders on binance USDⓈ-M / OKX SWAP
  2. A spot+perp coordinator that opens both legs atomically (we can't
     reuse HedgeCoordinator without meaningful changes — spot and perp
     sit on different ccxt clients, with different min-notionals and
     leverage defaults).
  3. Margin/collateral accounting so we never accidentally open a
     cross-margin position that can be liquidated.

Those are tracked in the strategy registry entry as V2 scope.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.common.clock import utcnow
from app.common.ids import new_opportunity_id
from app.common.logging import get_logger
from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager

log = get_logger("strategy.funding_rate")

_RING_MAX = 200

# Funding periods per year by exchange (binance USDⓈ-M = 3 per day → 1095).
_PERIODS_PER_YEAR = {
    "binance": 1095,
    "okx": 1095,
}


@dataclass(slots=True)
class FundingRateOpportunity:
    opportunity_id: str
    exchange: str
    symbol: str  # e.g. "BTC/USDT:USDT" (ccxt perp notation)
    funding_rate: Decimal  # per-period, e.g. 0.0001 = 0.01%
    apr_bps: Decimal  # annualised, bps
    direction: str  # "short-perp-long-spot" or "long-perp-short-spot"
    spot_ref_price: Decimal | None
    perp_mark_price: Decimal | None
    next_funding_time: datetime | None
    detected_at: datetime


def opp_to_dict(o: FundingRateOpportunity) -> dict:
    return {
        "opportunity_id": o.opportunity_id,
        "exchange": o.exchange,
        "symbol": o.symbol,
        "funding_rate": str(o.funding_rate),
        "apr_bps": str(o.apr_bps),
        "direction": o.direction,
        "spot_ref_price": str(o.spot_ref_price) if o.spot_ref_price is not None else None,
        "perp_mark_price": str(o.perp_mark_price) if o.perp_mark_price is not None else None,
        "next_funding_time": o.next_funding_time.isoformat() if o.next_funding_time else None,
        "detected_at": o.detected_at.isoformat(),
    }


class FundingRateScanner:
    """Poll-every-N-minutes funding rate scanner. Stateless except for ring."""

    def __init__(
        self,
        settings: Settings,
        book_mgr: OrderBookManager,
    ):
        self._settings = settings
        self._books = book_mgr
        self._running = False
        self._ring: deque[FundingRateOpportunity] = deque(maxlen=_RING_MAX)
        self._session_count = 0
        # Lazily-constructed ccxt.future clients keyed by exchange name so
        # we don't disrupt the spot adapters used by the rest of the system.
        self._perp_clients: dict[str, Any] = {}
        self._last_error: str | None = None
        self._last_poll_at: datetime | None = None

    # ---- public ----------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def recent(self, limit: int = 50) -> list[FundingRateOpportunity]:
        items = list(self._ring)
        items.reverse()
        return items[:limit]

    def session_count(self) -> int:
        return self._session_count

    def status(self) -> dict:
        return {
            "running": self._running,
            "enabled": self._settings.strategy_funding_rate_spot_perp_enabled,
            "session_count": self._session_count,
            "last_error": self._last_error,
            "last_poll_at": self._last_poll_at.isoformat() if self._last_poll_at else None,
            "min_apr_bps": str(self._settings.funding_rate_min_apr_bps),
            "exchange": self._settings.funding_rate_exchange,
            "symbols": [s.strip() for s in self._settings.funding_rate_symbols.split(",") if s.strip()],
        }

    @staticmethod
    def parse_symbols(raw: str) -> list[str]:
        """Parse ``funding_rate_symbols`` — comma-separated ccxt perp symbols."""
        return [s.strip() for s in raw.split(",") if s.strip()]

    # ---- internal --------------------------------------------------------

    def _get_client(self, exchange: str):
        if exchange in self._perp_clients:
            return self._perp_clients[exchange]
        try:
            import ccxt  # local import to avoid cold-path cost
        except Exception as e:  # noqa: BLE001
            log.error("ccxt_import_failed", error=str(e))
            raise

        if exchange == "binance":
            client = ccxt.binance(
                {
                    "enableRateLimit": True,
                    "options": {"defaultType": "future"},
                }
            )
        elif exchange == "okx":
            client = ccxt.okx(
                {
                    "enableRateLimit": True,
                    "options": {"defaultType": "swap"},
                }
            )
        else:
            raise ValueError(f"unsupported exchange for funding rate: {exchange}")
        self._perp_clients[exchange] = client
        return client

    async def _fetch_funding(self, exchange: str, symbol: str) -> dict | None:
        client = self._get_client(exchange)
        try:
            # ccxt is sync; off-load to a thread.
            res = await asyncio.wait_for(
                asyncio.to_thread(client.fetch_funding_rate, symbol),
                timeout=10.0,
            )
            return res
        except asyncio.TimeoutError:
            self._last_error = f"fetch_funding_rate timeout on {exchange}:{symbol}"
            return None
        except Exception as e:  # noqa: BLE001
            # Funding rate errors are typically "symbol not found" if the
            # user mis-typed. Log but keep scanning the rest.
            self._last_error = f"{exchange}:{symbol} fetch error: {e}"
            return None

    def _spot_ref(self, exchange: str, perp_symbol: str) -> Decimal | None:
        """Fetch the spot mid for the matching spot pair, if we have it."""
        # Strip the ccxt perp suffix (":USDT") to get the underlying spot pair.
        spot_symbol = perp_symbol.split(":", 1)[0]
        book = self._books.get(exchange, spot_symbol)
        if book is None or not book.asks or not book.bids:
            return None
        mid = (Decimal(str(book.asks[0].price)) + Decimal(str(book.bids[0].price))) / Decimal(2)
        return mid

    # ---- run loop --------------------------------------------------------

    async def run(self) -> None:
        self.start()
        while self._running:
            if not self._settings.strategy_funding_rate_spot_perp_enabled:
                self._running = False
                break
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("funding_scan_error", error=str(e))
                self._last_error = str(e)
            # Funding rates change slowly (every 8h) — polling every 5 min is
            # plenty and easy on the rate-limit budget.
            await asyncio.sleep(self._settings.funding_rate_poll_interval_sec)

    async def _scan_once(self) -> None:
        self._last_poll_at = utcnow()
        # Strategy-level exchange selection takes precedence.
        raw = (self._settings.strategy_funding_rate_spot_perp_exchanges or "").strip()
        selected = [x.strip() for x in raw.split(",") if x.strip()]
        ex = selected[0] if selected else self._settings.funding_rate_exchange
        symbols = self.parse_symbols(self._settings.funding_rate_symbols)
        if not symbols:
            return

        min_apr = Decimal(self._settings.funding_rate_min_apr_bps)
        periods = Decimal(_PERIODS_PER_YEAR.get(ex, 1095))

        for sym in symbols:
            res = await self._fetch_funding(ex, sym)
            if res is None:
                continue
            raw_rate = res.get("fundingRate") or res.get("funding_rate") or 0
            try:
                rate = Decimal(str(raw_rate))
            except Exception:  # noqa: BLE001
                continue
            apr_bps = abs(rate) * periods * Decimal(10000)
            if apr_bps < min_apr:
                continue

            direction = "short-perp-long-spot" if rate > 0 else "long-perp-short-spot"
            mark = res.get("markPrice") or res.get("indexPrice")
            mark_d = Decimal(str(mark)) if mark is not None else None
            nft_ms = res.get("nextFundingTime") or res.get("fundingDatetime")
            nft = None
            if isinstance(nft_ms, (int, float)):
                from datetime import timezone

                nft = datetime.fromtimestamp(nft_ms / 1000, tz=timezone.utc)

            opp = FundingRateOpportunity(
                opportunity_id=new_opportunity_id(),
                exchange=ex,
                symbol=sym,
                funding_rate=rate,
                apr_bps=apr_bps,
                direction=direction,
                spot_ref_price=self._spot_ref(ex, sym),
                perp_mark_price=mark_d,
                next_funding_time=nft,
                detected_at=utcnow(),
            )
            self._ring.append(opp)
            self._session_count += 1
            log.info(
                "funding_opp",
                id=opp.opportunity_id,
                symbol=sym,
                rate=str(rate),
                apr_bps=str(apr_bps),
            )
