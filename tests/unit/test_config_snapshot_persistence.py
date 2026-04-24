"""Regression for the ConfigService -> ConfigSnapshotRepo write-through.

Covers:
- ``persist`` with no repo attached is a no-op (no DB == degraded mode is OK)
- ``persist`` writes the full set of persistent keys (editable + strategy_*)
- ``load_from_db`` round-trips Decimal / bool / str / None values exactly
- ``load_from_db`` ignores unknown keys (e.g. snapshot from older schema)
- Strategy fields like ``strategy_cross_exchange_spot_exchanges`` survive
  the round-trip even though they're not in ``_EDITABLE_KEYS``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.config.settings import Settings
from app.services.config_service import ConfigService


def _settings() -> Settings:
    return Settings(
        postgres_dsn="postgresql+asyncpg://x:x@localhost/none",
        redis_url="redis://localhost:6379/0",
    )


class _FakeRepo:
    """In-memory stand-in for ConfigSnapshotRepo (no DB needed)."""

    def __init__(self, initial: dict[str, Any] | None = None):
        self._payload: dict[str, Any] | None = dict(initial) if initial else None
        self.save_calls = 0

    async def load(self) -> dict[str, Any] | None:
        return None if self._payload is None else dict(self._payload)

    async def save(self, payload: dict[str, Any], actor: str = "api"):
        self._payload = dict(payload)
        self.save_calls += 1


def test_persist_without_repo_is_noop():
    svc = ConfigService(_settings())
    # No repo attached → must not raise.
    import asyncio

    asyncio.run(svc.persist())


def test_persist_writes_editable_and_strategy_keys():
    svc = ConfigService(_settings())
    repo = _FakeRepo()
    svc.attach_repo(repo)

    svc.update({"min_net_edge_bps": "7"})
    setattr(svc.settings, "strategy_cross_exchange_spot_exchanges", "binance,okx,bybit")

    import asyncio

    asyncio.run(svc.persist())

    assert repo.save_calls == 1
    payload = repo._payload
    assert payload is not None
    # Editable key present.
    assert payload["min_net_edge_bps"] == "7"
    # Strategy key auto-discovered + present.
    assert payload["strategy_cross_exchange_spot_exchanges"] == "binance,okx,bybit"


def test_load_from_db_round_trips_values():
    # Persist with one ConfigService.
    svc1 = ConfigService(_settings())
    repo = _FakeRepo()
    svc1.attach_repo(repo)
    svc1.update({"min_net_edge_bps": "11", "paused": True})
    setattr(svc1.settings, "strategy_cross_exchange_spot_exchanges", "binance,kucoin,htx")

    import asyncio

    asyncio.run(svc1.persist())

    # Fresh ConfigService loads from the same repo and matches.
    svc2 = ConfigService(_settings())
    svc2.attach_repo(repo)
    assert svc2.settings.min_net_edge_bps != Decimal("11")  # default differs
    asyncio.run(svc2.load_from_db())

    assert svc2.settings.min_net_edge_bps == Decimal("11")
    assert svc2.settings.paused is True
    assert getattr(svc2.settings, "strategy_cross_exchange_spot_exchanges") == "binance,kucoin,htx"


def test_load_from_db_ignores_unknown_keys():
    svc = ConfigService(_settings())
    # Snapshot with a stale field that no longer exists in Settings.
    repo = _FakeRepo({"min_net_edge_bps": "5", "vestigial_field_v1": "xyz"})
    svc.attach_repo(repo)

    import asyncio

    applied = asyncio.run(svc.load_from_db())
    # One known field applied, the unknown one silently skipped.
    assert applied == 1
    assert svc.settings.min_net_edge_bps == Decimal("5")


def test_strategy_enabled_flag_persisted():
    """Toggle a strategy_*_enabled flag away from its default and confirm
    the flipped value is restored after a fresh load."""
    svc1 = ConfigService(_settings())
    repo = _FakeRepo()
    svc1.attach_repo(repo)
    default_value = getattr(svc1.settings, "strategy_cross_exchange_spot_enabled")
    flipped = not default_value
    setattr(svc1.settings, "strategy_cross_exchange_spot_enabled", flipped)

    import asyncio

    asyncio.run(svc1.persist())

    svc2 = ConfigService(_settings())
    svc2.attach_repo(repo)
    # Fresh Settings starts back at the default value.
    assert getattr(svc2.settings, "strategy_cross_exchange_spot_enabled") == default_value
    asyncio.run(svc2.load_from_db())
    # After loading the snapshot, the flipped value is restored.
    assert getattr(svc2.settings, "strategy_cross_exchange_spot_enabled") == flipped


def test_load_from_db_with_empty_payload_returns_zero():
    svc = ConfigService(_settings())
    repo = _FakeRepo({})
    svc.attach_repo(repo)

    import asyncio

    assert asyncio.run(svc.load_from_db()) == 0


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("min_net_edge_bps", "3.5", Decimal("3.5")),
        ("paused", True, True),
        ("kill_switch", False, False),
        ("scan_interval_ms", 250, 250),
    ],
)
def test_round_trip_value_types(key: str, value: Any, expected: Any):
    svc1 = ConfigService(_settings())
    repo = _FakeRepo()
    svc1.attach_repo(repo)
    svc1.update({key: value})

    import asyncio

    asyncio.run(svc1.persist())

    svc2 = ConfigService(_settings())
    svc2.attach_repo(repo)
    asyncio.run(svc2.load_from_db())
    assert getattr(svc2.settings, key) == expected
