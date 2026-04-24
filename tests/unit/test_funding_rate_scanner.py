"""Tests for FundingRateScanner parsing, threshold filter, and output shape.

We monkeypatch ``_fetch_funding`` so the tests never hit the network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.marketdata.orderbook_manager import OrderBookManager
from app.strategy.funding_rate_scanner import (
    FundingRateOpportunity,
    FundingRateScanner,
    opp_to_dict,
)


def _settings(**over) -> Settings:
    kwargs = dict(
        enabled_symbols="BTC/USDT",
        funding_rate_symbols="BTC/USDT:USDT,ETH/USDT:USDT",
        funding_rate_min_apr_bps=Decimal("500"),  # 5% APR
        strategy_funding_rate_spot_perp_enabled=True,
        strategy_funding_rate_spot_perp_exchanges="binance",
    )
    kwargs.update(over)
    return Settings(_env_file=None, **kwargs)


def test_parse_symbols_handles_whitespace_and_empties():
    parsed = FundingRateScanner.parse_symbols(" BTC/USDT:USDT, ETH/USDT:USDT ,, SOL/USDT:USDT")
    assert parsed == ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]


@pytest.mark.asyncio
async def test_scan_emits_opportunity_above_threshold(monkeypatch):
    s = _settings()
    books = OrderBookManager()
    scanner = FundingRateScanner(s, books)

    async def fake_fetch(exchange, symbol):
        # 0.03% per 8h period = ~0.0003 × 1095 periods/yr = 32.85% APY = 3285 bps
        return {
            "fundingRate": 0.0003,
            "markPrice": 50000 if symbol.startswith("BTC") else 3000,
            "nextFundingTime": int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
        }

    monkeypatch.setattr(scanner, "_fetch_funding", fake_fetch)

    await scanner._scan_once()

    recent = scanner.recent()
    assert len(recent) == 2
    btc = recent[0] if "BTC" in recent[0].symbol else recent[1]
    assert btc.apr_bps > Decimal("500")
    assert btc.direction == "short-perp-long-spot"
    d = opp_to_dict(btc)
    assert d["apr_bps"].startswith("3285") or d["apr_bps"].startswith("3,")


@pytest.mark.asyncio
async def test_scan_skips_below_threshold(monkeypatch):
    s = _settings(funding_rate_min_apr_bps=Decimal("10000"))  # 100% APR threshold
    books = OrderBookManager()
    scanner = FundingRateScanner(s, books)

    async def fake_fetch(exchange, symbol):
        # 0.0001 per 8h = 10.95% APR = below the 100% threshold
        return {"fundingRate": 0.0001, "markPrice": 50000, "nextFundingTime": 0}

    monkeypatch.setattr(scanner, "_fetch_funding", fake_fetch)

    await scanner._scan_once()
    assert len(scanner.recent()) == 0


@pytest.mark.asyncio
async def test_scan_direction_flips_on_negative_rate(monkeypatch):
    s = _settings()
    books = OrderBookManager()
    scanner = FundingRateScanner(s, books)

    async def fake_fetch(exchange, symbol):
        return {"fundingRate": -0.0003, "markPrice": 50000, "nextFundingTime": 0}

    monkeypatch.setattr(scanner, "_fetch_funding", fake_fetch)

    await scanner._scan_once()
    opps = scanner.recent()
    assert len(opps) == 2
    assert all(o.direction == "long-perp-short-spot" for o in opps)


def test_status_shape_is_complete():
    s = _settings()
    books = OrderBookManager()
    scanner = FundingRateScanner(s, books)
    st = scanner.status()
    assert "running" in st
    assert st["exchange"] == "binance"
    assert st["symbols"] == ["BTC/USDT:USDT", "ETH/USDT:USDT"]
