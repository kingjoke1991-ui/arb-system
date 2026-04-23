from __future__ import annotations

from app.adapters.ccxt_adapter import CcxtExchangeAdapter
from app.config.settings import Settings


def build_binance_adapter(settings: Settings) -> CcxtExchangeAdapter:
    import ccxt  # type: ignore

    klass = getattr(ccxt, "binance")
    client = klass(
        {
            "apiKey": settings.binance_api_key or "",
            "secret": settings.binance_api_secret or "",
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )
    if settings.binance_sandbox:
        try:
            client.set_sandbox_mode(True)
        except Exception:  # noqa: BLE001
            pass
    return CcxtExchangeAdapter(name="binance", ccxt_client=client)
