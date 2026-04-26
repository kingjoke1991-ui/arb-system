"""
Authoritative catalog of the exchanges this system knows how to talk to.

Single source of truth for:
- UI credential cards (one per entry)
- AdapterRegistry initialisation (builds a spot adapter per entry)
- PerpRegistry initialisation (only entries with supports_perp=True)
- Default fee table (default_taker_bps)
- Strategy bindings (per-strategy account selection uses this list)

Adding a new exchange = append one row here; no other file changes required,
provided the ``ccxt_id`` maps to a real ccxt class and our settings schema
contains the matching ``{id}_api_key`` etc. fields. See
``app/config/settings.py`` for the env-variable naming convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExchangeSpec:
    # Canonical id used in settings keys, URLs, registry lookups.
    id: str
    # Human-readable label for the UI.
    display_name: str
    # ccxt class name for the SPOT client. Looked up via ``getattr(ccxt, ...)``.
    ccxt_id: str
    # True = ccxt client requires a ``password`` (passphrase) field.
    requires_passphrase: bool
    # Approximate real-world taker fee in basis points (used when ccxt doesn't
    # surface a fee and the operator hasn't overridden it). Roughly calibrated
    # to the "retail, no VIP" tier.
    default_taker_bps: Decimal
    # True when this exchange ships a usable perpetual-swap API via ccxt.
    # False (e.g. Kraken, Coinbase spot-only classic) = hide from funding-rate
    # strategy binding.
    supports_perp: bool = False
    # ccxt class name for PERP. May equal ccxt_id (OKX / KuCoin / Bitget /
    # Huobi share one class for both) or differ (binance → binanceusdm).
    ccxt_perp_id: str = ""
    # True = perp ccxt client also needs a passphrase (usually follows the
    # spot side but kept explicit).
    perp_requires_passphrase: bool = False
    # Optional caveat string surfaced in the UI credentials card. Used to
    # call out exchanges with unusually high fees / weird symbol schemes.
    notes: str = ""


# Order of this list drives render order in the UI. Binance + OKX stay on top
# because they are the historical defaults and already have live credentials
# on most deployments.
SUPPORTED_EXCHANGES: list[ExchangeSpec] = [
    ExchangeSpec(
        id="binance",
        display_name="Binance",
        ccxt_id="binance",
        requires_passphrase=False,
        default_taker_bps=Decimal("10"),
        supports_perp=True,
        ccxt_perp_id="binanceusdm",
    ),
    ExchangeSpec(
        id="okx",
        display_name="OKX",
        ccxt_id="okx",
        requires_passphrase=True,
        default_taker_bps=Decimal("15"),
        supports_perp=True,
        ccxt_perp_id="okx",
        perp_requires_passphrase=True,
    ),
    ExchangeSpec(
        id="bybit",
        display_name="Bybit",
        ccxt_id="bybit",
        requires_passphrase=False,
        default_taker_bps=Decimal("10"),
        supports_perp=True,
        ccxt_perp_id="bybit",
    ),
    ExchangeSpec(
        id="gate",
        display_name="Gate.io",
        ccxt_id="gate",
        requires_passphrase=False,
        default_taker_bps=Decimal("20"),
        supports_perp=True,
        ccxt_perp_id="gate",
        notes="费率中等（20bps），部分小币流动性好",
    ),
    ExchangeSpec(
        id="kucoin",
        display_name="KuCoin",
        ccxt_id="kucoin",
        requires_passphrase=True,
        default_taker_bps=Decimal("10"),
        supports_perp=True,
        ccxt_perp_id="kucoinfutures",
        perp_requires_passphrase=True,
    ),
    ExchangeSpec(
        id="bitget",
        display_name="Bitget",
        ccxt_id="bitget",
        requires_passphrase=True,
        default_taker_bps=Decimal("10"),
        supports_perp=True,
        ccxt_perp_id="bitget",
        perp_requires_passphrase=True,
    ),
    ExchangeSpec(
        id="kraken",
        display_name="Kraken",
        ccxt_id="kraken",
        requires_passphrase=False,
        default_taker_bps=Decimal("26"),
        supports_perp=False,
        notes="费率较高（26bps taker），跨所套利空间小",
    ),
    ExchangeSpec(
        id="coinbase",
        display_name="Coinbase Advanced",
        ccxt_id="coinbase",
        requires_passphrase=True,
        default_taker_bps=Decimal("60"),
        supports_perp=False,
        notes="费率最高（Level 1 taker 60bps），仅建议做行情观察",
    ),
    # HTX (Huobi) was removed: ccxt.pro's htx ws implementation falls back
    # to REST polling for almost every alt symbol (PEPE/SHIB/FLOKI/ORDI/
    # TRUMP/...), generating constant ``orderbook_ws_fallback_to_rest``
    # warnings, eating CPU on the REST poll loop, and producing stale
    # books that caused single-leg-fill hedge failures (see PR #5 / PR
    # #12 history). Operators who still want HTX for funding-rate or
    # triangular strategies can re-add an ExchangeSpec entry; the rest
    # of the system is generic over the catalog.
]


def by_id(exchange_id: str) -> ExchangeSpec | None:
    for s in SUPPORTED_EXCHANGES:
        if s.id == exchange_id:
            return s
    return None


def spot_ids() -> list[str]:
    return [s.id for s in SUPPORTED_EXCHANGES]


def perp_ids() -> list[str]:
    return [s.id for s in SUPPORTED_EXCHANGES if s.supports_perp]
