# Architecture

## Layers

1. **Adapters** — uniform exchange contract (`ExchangeAdapter`). One implementation per venue; a `MockExchangeAdapter` exists for tests and dry-run demos.
2. **Market data** — `OrderBookManager` keeps the latest snapshot per `(exchange, symbol)`. Scanner reads here; it never talks to adapters directly.
3. **Accounts** — `BalanceManager` keeps a fresh balance cache; `AccountReconciler` detects drift.
4. **Strategy** — `FeeModel` + `SlippageModel` + `SpreadCalculator` + `OpportunityScanner`. Pure functions over `OrderBookSnapshot` → `ArbitrageOpportunity`.
5. **Risk** — `KillSwitch` + `CircuitBreaker` + `HealthGuard` + `ExposureManager` + `RiskEngine`. Decides `accepted` / `rejected` for each opportunity.
6. **Execution** — `HedgeCoordinator` owns the `HedgeGroup` state machine, delegates order submission to `OrderRouter`, and calls `RepairEngine` on residual exposure.
7. **Storage** — SQLAlchemy Async. Repositories are thin and specific.
8. **API** — FastAPI routers grouped by concern. Admin-authenticated for anything mutating.
9. **Runtime** — `bootstrap` wires everything into a `Container`, starts background loops (orderbook polling, balance refresh, scanner), and tears them down on shutdown.

## State machines

```
hedge_group: new → planning → submitting → hedging → (completed | failed_needs_repair → repairing → completed | aborted)
order:       created → submitted → (partially_filled | filled | cancelled | rejected | expired)
```

Unknown states escalate to `FAILED_NEEDS_REPAIR` and raise severity.

## Modes

- `dry-run`: scanner emits opportunities, risk decides, `HedgeCoordinator` goes through the full state machine, `OrderRouter` fabricates "filled" responses. No network.
- `paper-trade`: same, but `OrderRouter` uses `PaperFillEngine` which consumes the live orderbook.
- `live`: same, but `OrderRouter` forwards to the real `ExchangeAdapter.create_order`. Additional health gates apply.

## Data flow (one tick)

```
adapters --push--> OrderBookManager
                        │
                        ▼
                 OpportunityScanner --emit--> ArbitrageOpportunity
                        │
                        ▼
                   RiskEngine --accept/reject-->
                        │accepted
                        ▼
                 HedgeCoordinator.execute()
                        │
                        ▼
               OrderRouter.submit() x2 (buy/sell leg)
                        │
                        ▼
              compute net_position_base ≈ 0 ?
                  ├── yes → completed
                  └── no  → RepairEngine.repair()
```
