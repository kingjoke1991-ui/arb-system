# Runbook

## Starting

```bash
docker compose up -d --build
open http://localhost:8000/
```

## Enabling `live` (minimum checklist)

1. Confirm kill switch = OFF in the UI.
2. Confirm circuit breaker = ok.
3. Confirm all exchanges show `marketdata_ok=true` and `balance_ok=true`.
4. Reduce `max_notional_per_trade` to a number you are comfortable losing.
5. Ensure exchange API keys have **only spot trade** permissions.
6. Trigger a `POST /control/reconcile/balances` and confirm no drift.
7. Use the Control tab to switch mode to `paper-trade`. Let it run for at least 10 minutes and confirm opportunities + mock hedges look healthy.
8. Only then switch to `live`.

## Pausing

```bash
# via UI Control tab or:
curl -X POST -H "X-Admin-Token: $TOKEN" http://localhost:8000/control/kill-switch/on
```

## Rotating API keys

1. Update `.env`.
2. `POST /control/reload-config` (this re-reads env but does NOT re-instantiate ccxt clients).
3. If you need hot-swap: restart the process (docker compose restart app).

## Investigating a weird hedge

1. Look up the hedge group id in UI → Hedges → Recent.
2. Copy the id, open Orders tab, filter by `hedge_group_id`.
3. Check DB `system_events` for the same time window.
4. If `state=failed_needs_repair`, click Repair on the active hedge in the UI.
