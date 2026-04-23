# arb-system

Cross-exchange **crypto spot arbitrage** MVP. Implements a complete, observable, reviewable pipeline:

```
exchanges  →  market data  →  opportunity scanner  →  risk engine  →  hedge executor  →  storage/metrics
```

It is intentionally conservative: the default mode is `dry-run`. `live` mode is opt-in and gated by the kill switch, circuit breaker, health checks, and strict notional caps.

---

## Status & scope

- **Exchanges (MVP):** Binance + OKX (spot)
- **Pairs (default whitelist):** `BTC/USDT`, `ETH/USDT`, `SOL/USDT`
- **Modes:** `dry-run` (default) → `paper-trade` → `live`
- **Not in scope (v1):** on-chain DEXes, auto on-chain transfers, derivatives, rate-limit-sensitive HFT

---

## Architecture

```
┌──────────── Operations Panel (HTML/JS at /)  ────────────┐
│                                                          │
│   FastAPI  (/config /control /opportunities /hedges ...) │
└──────────────┬──────────────────────────────┬────────────┘
               │                              │
               ▼                              ▼
    ┌──── Dependency Container (bootstrap.py) ───────────┐
    │  AdapterRegistry (binance, okx, mock)              │
    │  OrderBookManager (poll loop, staleness)           │
    │  BalanceManager  (periodic refresh)                │
    │  OpportunityScanner (FeeModel + Slippage + Spread) │
    │  RiskEngine (KillSwitch + CircuitBreaker + Health) │
    │  HedgeCoordinator (state machine, 2-leg)           │
    │  RepairEngine    (unwinds net exposure)            │
    │  OrderRouter     (dry/paper/live)                  │
    │  Storage Repos   (Postgres via SQLAlchemy Async)   │
    │  Metrics         (Prometheus)                      │
    └───────────────────────────────────────────────────┘
```

See also `docs/architecture.md`, `docs/runbook.md`, `docs/configuration.md`, `docs/troubleshooting.md`.

---

## Quick start (Docker)

```bash
cp .env.example .env
# edit .env — mode defaults to dry-run so you can start without API keys
docker compose up -d --build
open http://localhost:8000/          # operations panel
open http://localhost:8000/docs      # API docs
open http://localhost:3000           # Grafana (admin/admin)
open http://localhost:9090           # Prometheus
```

## Local dev (no Docker)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# optional: start Postgres/Redis with docker compose up -d postgres redis

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Running the tests

```bash
make lint
make test
```

Tests run entirely on the mock adapter. No network or DB required.

---

## Operations panel

A minimal SPA served at `/` (backed by `/ui/*` static files). It covers:

- **Dashboard:** mode, kill switch, circuit breaker, active hedges, exchange health
- **Config:** every tuning knob with a tooltip; Save applies changes at runtime
- **Control:** mode switch, kill switch, circuit-breaker reset, reconcile, cooldown
- **Opportunities / Hedges / Orders / MarketData / Balances / Reports / Events**

All mutating endpoints require the `X-Admin-Token` header (set `ADMIN_API_TOKEN` in `.env`). Enter it once in the top-right of the panel.

---

## Modes

| Mode | Real orders | Real balances required | What runs |
|------|-------------|------------------------|-----------|
| `dry-run` | ❌ | ❌ | Market data, scanner, risk, full state-machine log |
| `paper-trade` | ❌ | virtual | Same + local fill engine using live book |
| `live` | ✅ | ✅ | Same + real order router + strict health gates |

Switch via the UI (Control tab) or:

```bash
curl -X POST -H "X-Admin-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"paper-trade"}' \
  http://localhost:8000/control/mode
```

---

## Safety model

- **Kill switch** — global veto, always available via `/control/kill-switch/{on,off}`.
- **Circuit breaker** — trips on N consecutive failures or failure ratio in the window.
- **Health guard** — each exchange must have fresh market data (and fresh balances in `live`).
- **Exposure manager** — caps notional in flight per exchange.
- **Cooldown** — per-symbol, prevents firing twice on the same micro-spread.
- **Max notional per trade** — hard cap, applied after all other sizing constraints.
- **Repair engine** — if the two legs fill asymmetrically, it unwinds the residual.
- **Live-mode guard** — requires kill switch off, no active circuit breaker, and all adapters healthy.

---

## Project layout

```
app/
  adapters/         Exchange adapters (ccxt-based, plus mock)
  marketdata/       Orderbook manager, staleness, symbol normalizer
  accounts/         Balance manager, reconciler
  strategy/         Fee model, slippage, spread, opportunity scanner
  risk/             Kill switch, circuit breaker, health guard, rules
  execution/        Hedge coordinator, router, repair, state machine, paper fills
  storage/          SQLAlchemy async engine, models, repositories
  services/         Metrics, alerts, reports, config
  api/              FastAPI routes
  runtime/          bootstrap + dependency container
  main.py           FastAPI app factory
frontend/static/    Operations panel (plain HTML/JS/CSS)
tests/              Unit, integration, replay
docker/             Prometheus config, Grafana provisioning
docs/               Architecture, configuration, runbook, troubleshooting
```

See `progress.md` for development log.
