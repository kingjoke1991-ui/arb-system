from __future__ import annotations

from app.adapters.ccxt_adapter import CcxtExchangeAdapter
from app.config.settings import Settings


def build_okx_adapter(settings: Settings) -> CcxtExchangeAdapter:
    import ccxt  # type: ignore

    klass = getattr(ccxt, "okx")
    client = klass(
        {
            "apiKey": settings.okx_api_key or "",
            "secret": settings.okx_api_secret or "",
            "password": settings.okx_passphrase or "",
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )
    if settings.okx_sandbox:
        try:
            client.set_sandbox_mode(True)
        except Exception:  # noqa: BLE001
            pass
    return CcxtExchangeAdapter(name="okx", ccxt_client=client)
