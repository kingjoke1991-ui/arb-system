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

    # Per-exchange API credentials. Keys follow ``{id}_api_key`` naming
    # convention matching ``app/adapters/exchanges_catalog.py``; adding a
    # new exchange = add a catalog entry + add matching fields here.
    # Kept flat (rather than nested) for easy `.env` overrides.
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_sandbox: bool = False

    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""
    okx_sandbox: bool = False

    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    bybit_sandbox: bool = False

    gate_api_key: str = ""
    gate_api_secret: str = ""
    gate_sandbox: bool = False

    kucoin_api_key: str = ""
    kucoin_api_secret: str = ""
    kucoin_passphrase: str = ""
    kucoin_sandbox: bool = False

    bitget_api_key: str = ""
    bitget_api_secret: str = ""
    bitget_passphrase: str = ""
    bitget_sandbox: bool = False

    kraken_api_key: str = ""
    kraken_api_secret: str = ""
    kraken_sandbox: bool = False

    coinbase_api_key: str = ""
    coinbase_api_secret: str = ""
    coinbase_passphrase: str = ""
    coinbase_sandbox: bool = False

    htx_api_key: str = ""
    htx_api_secret: str = ""
    htx_sandbox: bool = False

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
    bybit_perp_api_key: str = ""
    bybit_perp_api_secret: str = ""
    gate_perp_api_key: str = ""
    gate_perp_api_secret: str = ""
    kucoin_perp_api_key: str = ""
    kucoin_perp_api_secret: str = ""
    kucoin_perp_passphrase: str = ""
    bitget_perp_api_key: str = ""
    bitget_perp_api_secret: str = ""
    bitget_perp_passphrase: str = ""
    htx_perp_api_key: str = ""
    htx_perp_api_secret: str = ""

    # Funding-rate execution sizing + safety knobs.
    funding_max_notional_per_trade: Decimal = Decimal("50.0")
    # Maximum acceptable spot-perp basis (bps) at open time. If actual
    # basis exceeds this we refuse to open the hedge (protects against
    # opening into an already-dislocated book).
    funding_max_basis_bps: Decimal = Decimal("20")

    # Trading — tiered symbol whitelist. Final ``enabled_symbols`` is
    # the union of all enabled tiers. Individual tier lists are editable.
    # tier1: blue-chips (BTC/ETH/SOL) — included only if tier1_enabled.
    #   Spreads here are too tight for retail fees; default is observation.
    # tier2: mid-cap alts (DOGE/ARB/OP/SUI/WIF) — focus tier, higher hit rate.
    # tier3: small/meme (PEPE/SHIB/BONK/FLOKI/JTO) — high volatility, easy to
    #   see spreads but low per-trade notional and listings differ across
    #   exchanges (may trigger "symbol not found" on some venues).
    symbol_tier1: str = "BTC/USDT,ETH/USDT,SOL/USDT"
    symbol_tier2: str = "DOGE/USDT,ARB/USDT,OP/USDT,SUI/USDT,WIF/USDT,XRP/USDT,LINK/USDT,ADA/USDT"
    symbol_tier3: str = "PEPE/USDT,SHIB/USDT,BONK/USDT,FLOKI/USDT,JTO/USDT,TIA/USDT,ORDI/USDT"
    symbol_tier1_enabled: bool = True
    symbol_tier2_enabled: bool = True
    symbol_tier3_enabled: bool = True
    # Legacy field. If non-empty, overrides the tier-union (backwards compat).
    # Empty = compute from tiers.
    enabled_symbols: str = ""
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
    # Minimum tradable notional (USDT) at VWAP-fillable size. Guards against
    # "ghost" opportunities where the gross spread looks large but top-of-book
    # depth on one side is only a few USDT, so an actual fill would eat deep
    # into the book and realize far less edge than advertised.
    min_liquidity_usdt: Decimal = Decimal("20.0")
    min_order_size_quote: Decimal = Decimal("10.0")
    max_notional_per_trade: Decimal = Decimal("400.0")
    cooldown_seconds: int = 5
    scan_interval_ms: int = 200

    # Risk
    max_exposure_per_exchange: Decimal = Decimal("500.0")
    max_total_open_hedges: int = 3
    max_repair_attempts: int = 3
    max_consecutive_failures: int = 5
    max_marketdata_staleness_ms: int = 3000
    max_balance_staleness_sec: int = 60

    # Market-data transport. ccxt.pro merged into ccxt 1.95+ so all 9
    # spot adapters can stream order books over WebSocket without a paid
    # license. WS pushes book updates with ~10–100 ms latency vs the
    # 1.5–2.5 s queueing seen under heavy REST poll fan-out (162
    # polls / 200 ms on this 2-vCPU box).
    #   "auto"     — prefer WS, fall back to REST when WS errors
    #   "websocket"— WS with REST fallback (alias of auto today)
    #   "rest"     — never use WS (legacy / debug)
    marketdata_mode: str = "auto"
    # CSV blacklist of exchanges to keep on REST even when ws is enabled.
    # Useful if a particular exchange's WS feed is misbehaving in
    # production while leaving the others on the fast path.
    websocket_disabled_exchanges: str = ""
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
    #
    # cross_exchange_spot defaults to all 9 spot exchanges from the catalog so
    # the scan matrix is C(9,2)=36 pairs out of the box. Operator can narrow
    # selection via the UI and that override is written to .env on save.
    strategy_cross_exchange_spot_exchanges: str = "binance,okx,bybit,gate,kucoin,bitget,kraken,coinbase,htx"
    strategy_triangular_same_exchange_exchanges: str = "binance"
    strategy_cross_exchange_market_exchanges: str = "binance,okx,bybit,gate,kucoin,bitget,kraken,coinbase,htx"
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

    # Execution mode: how cross-exchange opportunities are filled.
    #   "taker_taker" (default, validated): both legs cross the spread
    #     simultaneously. Deterministic but pays 2× taker fee (~25bps
    #     on binance/okx). Fits tight-spread main pairs that fill fast.
    #   "maker_taker" (new, opt-in): post a maker buy on the cheap venue
    #     at best_bid + maker_offset_bps; on fill, taker-sell on the
    #     expensive venue immediately. Cuts fees roughly in half but
    #     adds three failure modes: queue-not-reached / price-drifts /
    #     single-leg-filled-hedge-fails. Each is handled by the state
    #     machine below but expect lower fill rate than taker_taker.
    execution_mode: Literal["taker_taker", "maker_taker"] = "taker_taker"

    # ---- Maker-taker knobs (only used when execution_mode=maker_taker) ----
    # Offset added to best_bid when posting maker buy (or subtracted from
    # best_ask for maker sell). Larger = more likely to fill but at worse
    # price; too large = may cross and become a taker (kills the whole point).
    maker_offset_bps: Decimal = Decimal("1.0")
    # Cancel the maker if it hasn't reached this fill ratio by max_wait_ms.
    # 1.0 = require full fill; lower values accept partials.
    min_fill_ratio: Decimal = Decimal("0.5")
    # Absolute timeout on waiting for the maker order to fill. Exceeded =
    # cancel and rebate the exposure budget.
    max_wait_ms: int = 5000
    # After the maker fills (or partials), hedge on the other side must
    # complete within this window, else we force-close the dangling leg
    # (reverse taker trade) to flatten exposure.
    hedge_timeout_ms: int = 2000
    # If the mid-price on either venue drifts by more than this many bps
    # from the price at opportunity-capture time while the maker is still
    # open, cancel the maker (the arb has evaporated).
    max_price_deviation_bps: Decimal = Decimal("3")
    # How often to poll the resting maker order for fills / status. Lower =
    # lower latency to act on fills; higher = lower rate-limit pressure.
    maker_poll_interval_ms: int = 250

    # Monitoring
    metrics_enabled: bool = True
    alert_webhook: str = ""
    alert_min_severity: Literal["info", "warning", "error", "critical"] = "warning"

    @field_validator("enabled_symbols", "symbol_tier1", "symbol_tier2", "symbol_tier3")
    @classmethod
    def _normalize_symbols(cls, v: str) -> str:
        return ",".join(s.strip().upper() for s in v.split(",") if s.strip())

    @property
    def enabled_symbol_list(self) -> list[str]:
        # If legacy enabled_symbols is explicitly set, honor it for backwards
        # compatibility with older operator workflows / .env files.
        if self.enabled_symbols:
            return [s for s in self.enabled_symbols.split(",") if s]
        # Otherwise compute the union of all enabled tiers, preserving order
        # and de-duplicating (a symbol can appear in multiple tiers).
        seen: set[str] = set()
        out: list[str] = []
        for enabled, raw in (
            (self.symbol_tier1_enabled, self.symbol_tier1),
            (self.symbol_tier2_enabled, self.symbol_tier2),
            (self.symbol_tier3_enabled, self.symbol_tier3),
        ):
            if not enabled:
                continue
            for s in raw.split(","):
                s = s.strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
        return out

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
