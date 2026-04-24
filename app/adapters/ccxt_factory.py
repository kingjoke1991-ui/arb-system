"""
Generic ccxt adapter factory. Reads settings attributes by convention
(``{id}_api_key`` / ``_api_secret`` / ``_passphrase`` / ``_sandbox``) so
adding a new exchange = one catalog entry + matching settings fields.

Separate from the old ``build_binance_adapter`` / ``build_okx_adapter``
helpers, which are kept as thin wrappers for backwards compatibility.
"""

from __future__ import annotations

from app.adapters.ccxt_adapter import CcxtExchangeAdapter
from app.adapters.exchanges_catalog import ExchangeSpec
from app.common.logging import get_logger
from app.config.settings import Settings

log = get_logger("adapters.factory")


def _s(settings: Settings, key: str) -> str:
    """Return attribute or empty string (missing attributes tolerated so old
    .env files without new exchange fields don't explode)."""
    return getattr(settings, key, "") or ""


def _b(settings: Settings, key: str) -> bool:
    return bool(getattr(settings, key, False))


def build_spot_adapter(
    settings: Settings, spec: ExchangeSpec
) -> CcxtExchangeAdapter | None:
    """Build a spot ccxt adapter for ``spec``. Returns ``None`` if ccxt
    doesn't know this exchange (so the caller can fall back gracefully).

    The adapter is always built, even without an API key, so the scanner
    can read public orderbooks. ``is_configured`` distinguishes live-
    capable adapters (has key) from public-only ones.
    """
    try:
        import ccxt  # type: ignore
    except Exception as e:  # noqa: BLE001
        log.warning("ccxt_unavailable", error=str(e))
        return None

    klass = getattr(ccxt, spec.ccxt_id, None)
    if klass is None:
        log.warning("ccxt_class_missing", exchange=spec.id, ccxt_id=spec.ccxt_id)
        return None

    opts: dict = {
        "apiKey": _s(settings, f"{spec.id}_api_key"),
        "secret": _s(settings, f"{spec.id}_api_secret"),
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    if spec.requires_passphrase:
        opts["password"] = _s(settings, f"{spec.id}_passphrase")

    try:
        client = klass(opts)
    except Exception as e:  # noqa: BLE001
        log.warning("ccxt_client_init_failed", exchange=spec.id, error=str(e))
        return None

    if _b(settings, f"{spec.id}_sandbox"):
        try:
            client.set_sandbox_mode(True)
        except Exception:  # noqa: BLE001
            pass

    return CcxtExchangeAdapter(
        name=spec.id,
        ccxt_client=client,
        default_fee_bps=spec.default_taker_bps,
    )
