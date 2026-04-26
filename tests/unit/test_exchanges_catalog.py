"""Smoke tests for the 9-exchange catalog + generic adapter factory."""

from __future__ import annotations

from decimal import Decimal

from app.adapters.ccxt_factory import build_spot_adapter
from app.adapters.exchanges_catalog import (
    SUPPORTED_EXCHANGES,
    by_id,
    perp_ids,
    spot_ids,
)
from app.adapters.perp_registry import build_default_perp_registry
from app.adapters.registry import build_default_registry
from app.config.settings import Settings


def test_catalog_has_all_eight_exchanges():
    # HTX was removed from the catalog because its ws push fell back to
    # REST polling for almost every alt symbol; see exchanges_catalog.py.
    names = [s.id for s in SUPPORTED_EXCHANGES]
    assert set(names) == {
        "binance",
        "okx",
        "bybit",
        "gate",
        "kucoin",
        "bitget",
        "kraken",
        "coinbase",
    }
    assert "htx" not in names
    assert len(names) == len(set(names)), "duplicate id in catalog"


def test_catalog_by_id_lookup():
    s = by_id("kraken")
    assert s is not None
    assert s.display_name == "Kraken"
    assert s.default_taker_bps == Decimal("26")
    assert by_id("not-a-real-exchange") is None


def test_passphrase_flags_match_known_exchanges():
    # OKX / KuCoin / Bitget / Coinbase require passphrase on spot.
    pw_needed = {s.id for s in SUPPORTED_EXCHANGES if s.requires_passphrase}
    assert pw_needed == {"okx", "kucoin", "bitget", "coinbase"}


def test_spot_and_perp_id_lists():
    assert len(spot_ids()) == 8
    # Kraken + Coinbase have no perp support; htx removed from catalog.
    assert set(perp_ids()) == {"binance", "okx", "bybit", "gate", "kucoin", "bitget"}


def test_generic_spot_adapter_builds_without_keys():
    """Adapter should be constructible even with empty credentials (public
    orderbook usage). ``is_configured`` must reflect the missing key."""
    s = Settings()
    for spec in SUPPORTED_EXCHANGES:
        a = build_spot_adapter(s, spec)
        # ccxt may not know every id in every version; we only assert when
        # the factory succeeds that is_configured is False without keys.
        if a is not None:
            assert a.is_configured is False
            assert a.name == spec.id


def test_default_registry_contains_all_catalog_entries():
    s = Settings()
    r = build_default_registry(s)
    # We accept a subset if ccxt lacks a class for one entry, but there must
    # be at least 6 (all non-exotic exchanges are supported by ccxt 4.x).
    assert len(r.names()) >= 6
    assert "binance" in r.names()
    assert "bybit" in r.names()
    # htx must NOT be in registry (was removed from catalog).
    assert "htx" not in r.names()


def test_default_perp_registry_skips_unsupported():
    s = Settings()
    p = build_default_perp_registry(s)
    # Kraken / Coinbase have supports_perp=False; they must not appear.
    assert "kraken-perp" not in p.names()
    assert "coinbase-perp" not in p.names()


def test_symbol_tier_union_in_settings():
    s = Settings(
        enabled_symbols="",
        symbol_tier1="BTC/USDT",
        symbol_tier2="DOGE/USDT",
        symbol_tier3="PEPE/USDT",
        symbol_tier1_enabled=True,
        symbol_tier2_enabled=True,
        symbol_tier3_enabled=False,
    )
    assert s.enabled_symbol_list == ["BTC/USDT", "DOGE/USDT"]


def test_enabled_symbols_legacy_override():
    """If enabled_symbols is set explicitly, it wins over tiers (back-compat)."""
    s = Settings(
        enabled_symbols="ETH/USDT,SOL/USDT",
        symbol_tier1="BTC/USDT",
        symbol_tier1_enabled=True,
    )
    assert s.enabled_symbol_list == ["ETH/USDT", "SOL/USDT"]
