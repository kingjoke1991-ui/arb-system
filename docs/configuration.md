# Configuration reference

All knobs live in `app/config/settings.py` and are surfaced through the UI (Config tab).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `dry-run` | `dry-run` / `paper-trade` / `live` |
| `enabled_symbols` | csv | `BTC/USDT,ETH/USDT,SOL/USDT` | Whitelist |
| `min_net_edge_bps` | decimal | `8` | Filter below this net edge |
| `min_profit_quote` | decimal | `1.0` | Minimum expected profit (quote) |
| `min_order_size_quote` | decimal | `10.0` | Minimum notional per leg |
| `max_notional_per_trade` | decimal | `50.0` | Hard cap per hedge group (quote) |
| `cooldown_seconds` | int | `5` | Per-symbol cooldown |
| `scan_interval_ms` | int | `200` | Scanner loop interval |
| `max_exposure_per_exchange` | decimal | `500.0` | In-flight exposure cap |
| `max_total_open_hedges` | int | `3` | Concurrent hedge groups |
| `max_repair_attempts` | int | `3` | Repair retries before abort |
| `max_consecutive_failures` | int | `5` | Circuit breaker trip threshold |
| `max_marketdata_staleness_ms` | int | `3000` | Snapshot considered stale |
| `max_balance_staleness_sec` | int | `60` | Balance considered stale (live only) |
| `kill_switch` | bool | `false` | Global veto |
| `order_type_policy` | string | `ioc_limit` | `limit` / `market` / `ioc_limit` / `fok_limit` |
| `ioc_price_buffer_bps` | decimal | `3` | Aggressive offset for IOC price |
| `cancel_timeout_ms` | int | `2000` | Used by future cancel-and-replace loops |
| `order_status_poll_ms` | int | `500` | Same |
| `alert_min_severity` | string | `warning` | Min severity to send alerts |

## API keys

Set in `.env`:

```
BINANCE_API_KEY=
BINANCE_API_SECRET=
OKX_API_KEY=
OKX_API_SECRET=
OKX_PASSPHRASE=
```

For MVP, recommended permissions: **spot trading only**, no withdrawals.

## Admin token

```
ADMIN_API_TOKEN=change-me-please
```

Required on every `POST /control/*`, `POST /config`, `POST /hedges/{id}/repair`. The UI asks for this in the top-right box.
