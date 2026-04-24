"""Perpetual-futures ccxt adapter.

We subclass ``CcxtExchangeAdapter`` rather than reimplement it: 95% of the
contract (orderbook fetch, create/cancel/fetch order, fee_rate, balances)
is identical to spot. The only two additions are:

* ``fetch_funding_rate(symbol)``: live funding rate + next settlement ts
  for a given perp symbol (ccxt-native).
* ``fetch_position(symbol)``: the currently-open position, used by the
  spot-perp coordinator to verify our perp leg really closed after a
  settlement cycle.

The adapter is wired through a **separate** ``PerpRegistry`` so the
spot-only code paths (cross-exchange scanner, BalanceManager's default
loop, HedgeCoordinator) never accidentally reach for a perp adapter.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from app.adapters.ccxt_adapter import CcxtExchangeAdapter, _bounded
from app.common.exceptions import TransientError
from app.common.logging import get_logger
from app.config.settings import Settings

log = get_logger("adapter.perp")


_DEFAULT_FUNDING_TIMEOUT_S = 10.0
_DEFAULT_POSITION_TIMEOUT_S = 5.0


class PerpAdapter(CcxtExchangeAdapter):
    """Perpetual-futures adapter. Mostly a CcxtExchangeAdapter with an
    extra ``fetch_funding_rate`` method."""

    async def fetch_funding_rate(self, symbol: str) -> dict[str, Any] | None:
        """Return the raw ccxt ``fundingRate`` struct. ``None`` on error."""
        if not hasattr(self._client, "fetch_funding_rate"):
            return None
        try:
            return await _bounded(
                asyncio.to_thread(self._client.fetch_funding_rate, symbol),
                timeout_s=_DEFAULT_FUNDING_TIMEOUT_S,
                kind="fetch_funding_rate",
            )
        except TransientError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(
                "fetch_funding_rate_failed",
                exchange=self.name,
                symbol=symbol,
                error=str(e),
            )
            return None

    async def fetch_position(self, symbol: str) -> dict[str, Any] | None:
        """Return the currently-open ccxt position, or ``None`` if flat."""
        if not self.is_configured:
            return None
        if not hasattr(self._client, "fetch_position"):
            return None
        try:
            pos = await _bounded(
                asyncio.to_thread(self._client.fetch_position, symbol),
                timeout_s=_DEFAULT_POSITION_TIMEOUT_S,
                kind="fetch_position",
            )
        except TransientError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(
                "fetch_position_failed",
                exchange=self.name,
                symbol=symbol,
                error=str(e),
            )
            return None
        contracts = pos.get("contracts") if pos else None
        try:
            if contracts is None or Decimal(str(contracts)) == 0:
                return None
        except Exception:  # noqa: BLE001
            return None
        return pos


def build_binance_perp_adapter(settings: Settings) -> PerpAdapter | None:
    """Construct a Binance USDⓈ-M futures adapter. Returns ``None`` if ccxt
    isn't available (unit-test / minimal container)."""
    try:
        import ccxt  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    klass = getattr(ccxt, "binance")
    client = klass(
        {
            "apiKey": settings.binance_perp_api_key or "",
            "secret": settings.binance_perp_api_secret or "",
            "enableRateLimit": True,
            # defaultType=future routes all requests through /fapi/*.
            "options": {"defaultType": "future"},
        }
    )
    if settings.binance_sandbox:
        try:
            client.set_sandbox_mode(True)
        except Exception:  # noqa: BLE001
            pass
    # Perp accounts are billed at a different fee tier than spot. 4 bps
    # is a realistic default for USDⓈ-M taker tier 0.
    return PerpAdapter(name="binance-perp", ccxt_client=client, default_fee_bps=Decimal("4"))


def build_okx_perp_adapter(settings: Settings) -> PerpAdapter | None:
    try:
        import ccxt  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    klass = getattr(ccxt, "okx")
    client = klass(
        {
            "apiKey": settings.okx_perp_api_key or "",
            "secret": settings.okx_perp_api_secret or "",
            "password": settings.okx_perp_passphrase or "",
            "enableRateLimit": True,
            # defaultType=swap routes to OKX perpetual swap endpoints.
            "options": {"defaultType": "swap"},
        }
    )
    if settings.okx_sandbox:
        try:
            client.set_sandbox_mode(True)
        except Exception:  # noqa: BLE001
            pass
    return PerpAdapter(name="okx-perp", ccxt_client=client, default_fee_bps=Decimal("5"))
