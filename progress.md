# Engineering Log (`progress.md`)

This file is kept up to date through every phase so that the state of the system is always legible.

---

## Phase 0 — Scaffolding

**Delivered:**
- Repo skeleton `app/{common,config,models,adapters,marketdata,accounts,strategy,risk,execution,storage,services,api,runtime,scripts}`.
- Python 3.11 project (`pyproject.toml`), `ruff`, `pytest`, `mypy`.
- Dockerfile, `docker-compose.yml` (postgres + redis + app + prometheus + grafana).
- Structured logging (`structlog`), Pydantic Settings config, `.env.example`.
- FastAPI app factory `app/main.py` with `/health` routes.
- Postgres DDL via `Base.metadata.create_all` applied at startup (simple for MVP; Alembic can come later).

**Verification:**
- `make lint && make test` pass locally.
- `docker compose up` brings the stack up; `GET /health` returns `{"status":"ok"}`.

---

## Phase 1 — Exchange adapters + market data

**Delivered:**
- Abstract `ExchangeAdapter`, CCXT-backed `CcxtExchangeAdapter`, `MockExchangeAdapter`.
- `build_binance_adapter`, `build_okx_adapter` using spot, taker fees.
- `OrderBookManager` with poll loop, staleness, subscription queues.
- `BalanceManager` with periodic refresh + virtual-balance helpers for paper-trade.
- `SymbolNormalizer` (pass-through for Binance/OKX canonical).
- `AccountReconciler` that detects drift.

**Notes:**
- We do not require `ccxt.pro`; `CcxtExchangeAdapter` wraps the sync ccxt client with `asyncio.to_thread`. Upgrading to `ccxt.pro` is a drop-in replacement later.
- Errors are classified as `TransientError` / `PermanentError` / `RateLimitError` / `AuthError` so higher layers can decide retry policy.

---

## Phase 2 — Opportunity scanner

**Delivered:**
- `FeeModel` with adapter-reported rate preference + table fallback.
- `SlippageModel` with VWAP over top-N levels.
- `SpreadCalculator` returning `SpreadEstimate` (includes `fillable`, `net_edge_bps`, `expected_profit_quote`).
- `OpportunityScanner` loops over every (symbol, buy_exchange, sell_exchange) pair.
- Persisted opportunities to `opportunities` table (with `decision`/`decision_reason`).

---

## Phase 3 — Risk & decision

**Delivered:**
- `KillSwitch`, `CircuitBreaker` (consecutive + rolling-window ratio), `HealthGuard`, `ExposureManager`, `cooldown`.
- `RiskEngine.evaluate(opp)` returns `RiskDecision`:
  - kill switch / circuit breaker
  - symbol whitelist
  - cooldown
  - min-edge, min-profit, min-order-size
  - max notional per trade
  - exposure per exchange
  - concurrent open-hedges cap
  - (live) fresh balances required
  - approved_amount = min of all constraints

---

## Phase 4 — dry-run execution

**Delivered:**
- `HedgeGroupState` + `HedgeStateMachine` (and `OrderStateMachine`).
- `HedgeCoordinator.execute(opp, amount)` handles planning → submitting → hedging → (completed | failed_needs_repair).
- `OrderRouter.submit(intent)` short-circuits in `dry-run` to "filled-at-price" without calling any exchange, while still emitting the full audit trail.

---

## Phase 5 — paper-trade execution

**Delivered:**
- `PaperFillEngine` walks the live book to decide fill amount, can simulate partial fills.
- `RepairEngine` computes `net_position_base`, submits a corrective trade on the side with overhang, tagged `is_repair=true`, capped at `max_repair_attempts`.
- Integration test `test_partial_fill_repair.py` verifies the repair path end-to-end.

---

## Phase 6 — live execution guard rails

**Delivered:**
- `OrderRouter` in `live` mode forwards `OrderIntent` to the selected adapter's `create_order` (ccxt).
- Client order IDs are short, hedge-group-scoped (`<hid>-b-xxxx` / `-s-xxxx` / `-r-xxxx`) for idempotency.
- Live mode requires kill switch off + no circuit-breaker trip + healthy market & balance feeds.
- Default `order_type_policy=ioc_limit` with `ioc_price_buffer_bps=3` gives IOC orders a ~3bp price cushion to cross.

**Not included (by design):**
- Fully async cancel-and-reprice loops — out of scope for MVP.
- Cross-exchange auto-transfer.

---

## Phase 7 — reconcile, reports, monitoring

**Delivered:**
- `AccountReconciler` detects per-asset drift and is exposed via `POST /control/reconcile/balances`.
- `ReportService.summary(hours)` aggregates opportunity count, accepted count, hedges completed/aborted, realized PnL, order count.
- Prometheus metrics: marketdata, opportunities, orders, risk rejects, kill switch, PnL counters. `GET /metrics` exposes them.
- Grafana is wired in compose; dashboard provisioning directory is reserved for follow-up.
- System events can be logged via `EventRepo.log(...)`.

---

## Operations panel UI

**Delivered:**
- Plain HTML/JS/CSS, no bundler. Served by FastAPI at `/`.
- Tabs: Dashboard, Config (every knob with a tooltip), Control, Opportunities, Hedges, Orders, MarketData, Balances, Reports, Events.
- All mutating actions go through `X-Admin-Token` (the token entered in the top-right of the panel).

---

## Tests

- **Unit**: symbol normalizer, state machine (hedge + order), slippage VWAP, spread calculator, risk rules, circuit breaker, fee model.
- **Integration**: full paper-trade flow, partial-fill → repair.
- **Replay**: deterministic orderbook tick replay verifies the scanner picks up the expected number of opportunities.

CI runs `ruff check`, `ruff format --check`, and `pytest -q` on every push/PR.

---

## Known limitations

- Uses `ccxt` (sync) via `asyncio.to_thread` rather than `ccxt.pro` (WebSocket). Fine for MVP; easy upgrade path.
- DDL is applied via `create_all`. If the schema evolves, switch to Alembic.
- Reports are minimal — one aggregate endpoint per window; richer dashboards live in Grafana.
