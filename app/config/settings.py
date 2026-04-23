"""
Application configuration.

Precedence: env vars > .env file > defaults.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["dry-run", "paper-trade", "live"]


class ExchangeCreds(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    sandbox: bool = False

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Global
    app_name: str = "arb-system"
    env: str = "dev"
    mode: Mode = "dry-run"
    timezone: str = "UTC"
    log_level: str = "INFO"

    # API auth
    admin_api_token: str = "change-me-please"

    # Storage
    postgres_dsn: str = "postgresql+asyncpg://arb:arb@localhost:5432/arb"
    redis_url: str = "redis://localhost:6379/0"

    # Binance
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_sandbox: bool = False

    # OKX
    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""
    okx_sandbox: bool = False

    # Trading
    enabled_symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT"
    min_net_edge_bps: Decimal = Decimal("8")
    min_profit_quote: Decimal = Decimal("1.0")
    min_order_size_quote: Decimal = Decimal("10.0")
    max_notional_per_trade: Decimal = Decimal("50.0")
    cooldown_seconds: int = 5
    scan_interval_ms: int = 200

    # Risk
    max_exposure_per_exchange: Decimal = Decimal("500.0")
    max_total_open_hedges: int = 3
    max_repair_attempts: int = 3
    max_consecutive_failures: int = 5
    max_marketdata_staleness_ms: int = 3000
    max_balance_staleness_sec: int = 60
    kill_switch: bool = False

    # Execution
    order_type_policy: Literal["limit", "market", "ioc_limit", "fok_limit"] = "ioc_limit"
    ioc_price_buffer_bps: Decimal = Decimal("3")
    cancel_timeout_ms: int = 2000
    order_status_poll_ms: int = 500

    # Monitoring
    metrics_enabled: bool = True
    alert_webhook: str = ""
    alert_min_severity: Literal["info", "warning", "error", "critical"] = "warning"

    @field_validator("enabled_symbols")
    @classmethod
    def _normalize_symbols(cls, v: str) -> str:
        return ",".join(s.strip().upper() for s in v.split(",") if s.strip())

    @property
    def enabled_symbol_list(self) -> list[str]:
        return [s for s in self.enabled_symbols.split(",") if s]

    @property
    def binance(self) -> ExchangeCreds:
        return ExchangeCreds(
            api_key=self.binance_api_key,
            api_secret=self.binance_api_secret,
            sandbox=self.binance_sandbox,
        )

    @property
    def okx(self) -> ExchangeCreds:
        return ExchangeCreds(
            api_key=self.okx_api_key,
            api_secret=self.okx_api_secret,
            passphrase=self.okx_passphrase,
            sandbox=self.okx_sandbox,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
