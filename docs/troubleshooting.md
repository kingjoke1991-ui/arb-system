# Troubleshooting

## `health/dependencies` returns `db=false`
- Check Postgres is up: `docker compose ps`.
- Connection DSN: `postgresql+asyncpg://USER:PASS@HOST:5432/DB`.
- The app will still run without the DB, but nothing is persisted.

## No opportunities detected
- Check `/marketdata/books` — are both exchanges returning live snapshots?
- Inspect `net_edge_bps_histogram` in Prometheus. If everything is below `min_net_edge_bps`, lower the threshold (UI Config tab) or widen the spread by choosing a less-liquid pair.
- Check `stale` flag — rate-limit or network issues will flip it to `true`.

## Orders keep rejecting with `below_min_size`
- Raise `max_notional_per_trade` so the approved amount crosses `min_order_size_quote`.
- Or lower `min_order_size_quote` if your exchange actually accepts smaller trades.

## Circuit breaker keeps tripping
- Look at `system_events` and recent order errors.
- Confirm your API keys are correct and have spot trading.
- Click "Reset" in the UI after fixing the root cause.

## `live` mode refuses to execute
- The risk engine blocks if `kill_switch=on`, the breaker is tripped, or any exchange is unhealthy.
- Check the Dashboard tiles — each should be green.

## Paper trade leaves a non-zero `net_position_base`
- That's the repair engine being careful. Look at `hedges/recent`, inspect `repair_attempts`.
- If attempts are exhausted, the group is `aborted`. Adjust balances and re-run.
