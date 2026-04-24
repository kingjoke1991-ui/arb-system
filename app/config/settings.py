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
    mode: Mode = "paper-trade"
    timezone: str = "UTC"
    log_level: str = "INFO"

    # API auth
    admin_api_token: str = "change-me-please"
    admin_rate_limit_enabled: bool = True
    admin_rate_limit_per_minute: int = 60

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

    # Perpetual-futures API keys. Kept separate from spot because exchanges
    # commonly scope keys per product line (Binance USDⓈ-M vs spot is a
    # different API key; OKX Unified can re-use the spot key but we keep
    # the fields distinct for least-privilege). Used only by
    # FundingRateScanner and FundingExecutor.
    binance_perp_api_key: str = ""
    binance_perp_api_secret: str = ""
    okx_perp_api_key: str = ""
    okx_perp_api_secret: str = ""
    okx_perp_passphrase: str = ""

    # Funding-rate execution sizing + safety knobs.
    funding_max_notional_per_trade: Decimal = Decimal("50.0")
    # Maximum acceptable spot-perp basis (bps) at open time. If actual
    # basis exceeds this we refuse to open the hedge (protects against
    # opening into an already-dislocated book).
    funding_max_basis_bps: Decimal = Decimal("20")

    # Trading
    enabled_symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT"
    min_net_edge_bps: Decimal = Decimal("3")
    min_profit_quote: Decimal = Decimal("0.1")
    # Verification override: when set to a non-negative value, the fee model
    # returns this value (in bps) for every (exchange, symbol, side) instead
    # of asking the adapter/table. Intended for smoke-testing the execution
    # chain without waiting for real arbitrage windows. Set to None (the
    # default) to use real exchange-reported fees.
    fee_override_bps: Decimal | None = None
    # Safety buffer subtracted from gross edge after fees & slippage.
    # Protects against micro-mid-shift between decision and order-placement.
    # Lower values make the scanner more eager; higher values more conservative.
    scan_buffer_bps: Decimal = Decimal("2")
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
    # Independent of mode: when true, the scanner skips risk/execute entirely.
    # Useful to "freeze" the system in paper/live without flipping mode back
    # to dry-run (which would lose operational context).
    paused: bool = False

    # Strategies (see app/strategy/registry.py for the catalog).
    # Enabling a strategy whose registry status != READY is rejected by the API.
    strategy_cross_exchange_spot_enabled: bool = True
    strategy_triangular_same_exchange_enabled: bool = False
    strategy_cross_exchange_market_enabled: bool = False
    strategy_funding_rate_spot_perp_enabled: bool = False
    strategy_stat_arb_pair_enabled: bool = False

    # Per-strategy selected exchanges. Comma-separated list of adapter names
    # (e.g. "binance,okx"). Empty string means "use all registered adapters"
    # for backwards compatibility at first boot. The /strategies API enforces
    # each strategy's min/max constraint on write.
    strategy_cross_exchange_spot_exchanges: str = "binance,okx"
    strategy_triangular_same_exchange_exchanges: str = "binance"
    strategy_cross_exchange_market_exchanges: str = "binance,okx"
    strategy_funding_rate_spot_perp_exchanges: str = "binance"
    strategy_stat_arb_pair_exchanges: str = "binance"

    # Triangular arbitrage scanner config. One exchange at a time, multiple
    # triangles supported. Triangle is an ordered 3-tuple of assets (A, B, C)
    # with USDT-style quote as A. Scanner will look for cycle A -> B -> C -> A
    # via pairs B/A, C/A, C/B (auto-derived).
    triangular_exchange: str = "binance"
    triangular_triangles: str = "USDT,BTC,ETH;USDT,BTC,SOL"
    triangular_min_net_edge_bps: Decimal = Decimal("2")

    # Funding-rate arbitrage scanner config. The scanner periodically reads
    # the current funding rate for each configured perp symbol and flags an
    # opportunity when |rate| * APY factor >= min_apr_bps (default 500bps =
    # 5% annualised). Execution is NOT enabled (detect-only V1) — filling
    # this legs requires a perp adapter + spot-perp coordinator; see the
    # funding_rate_spot_perp entry in app/strategy/registry.py for scope.
    funding_rate_exchange: str = "binance"
    funding_rate_symbols: str = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT"
    funding_rate_min_apr_bps: Decimal = Decimal("200")  # 2% APR (lowered for verification/testing)
    funding_rate_poll_interval_sec: int = 300  # 5 min (funding changes slowly)

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
