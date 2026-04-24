"""Regression for L3 (config bounds validation) and H5 (audit persistence)."""

from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.services.config_service import ConfigService


def _svc() -> ConfigService:
    s = Settings(
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )
    return ConfigService(s)


def test_min_net_edge_bps_negative_rejected():
    svc = _svc()
    with pytest.raises(ValueError):
        svc.update({"min_net_edge_bps": "-1"})


def test_min_net_edge_bps_too_large_rejected():
    svc = _svc()
    with pytest.raises(ValueError):
        svc.update({"min_net_edge_bps": "10000"})


def test_scan_interval_below_minimum_rejected():
    svc = _svc()
    with pytest.raises(ValueError):
        svc.update({"scan_interval_ms": 10})


def test_invalid_mode_rejected():
    svc = _svc()
    with pytest.raises(ValueError):
        svc.update({"mode": "god-mode"})


def test_valid_update_applied():
    svc = _svc()
    applied = svc.update({"min_net_edge_bps": "12"})
    assert "min_net_edge_bps" in applied
    assert svc.current()["min_net_edge_bps"] == Decimal("12")


def test_paused_flag_editable():
    svc = _svc()
    applied = svc.update({"paused": True})
    assert applied["paused"]["new"] == "True"
    assert svc.current()["paused"] is True
