"""Prometheus metrics registry."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()

        # Market data
        self.md_messages_total = Counter(
            "marketdata_messages_total",
            "Total orderbook updates received",
            ["exchange", "symbol"],
            registry=self.registry,
        )
        self.md_latency_ms = Histogram(
            "marketdata_latency_ms",
            "Exchange->local latency in ms",
            ["exchange", "symbol"],
            buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000),
            registry=self.registry,
        )
        self.md_stale_count = Counter(
            "marketdata_stale_count",
            "Orderbook staleness events",
            ["exchange", "symbol"],
            registry=self.registry,
        )

        # Opportunities
        self.opp_detected_total = Counter(
            "opportunities_detected_total",
            "Total opportunities detected",
            ["symbol"],
            registry=self.registry,
        )
        self.opp_accepted_total = Counter(
            "opportunities_accepted_total",
            "Opportunities accepted by risk",
            ["symbol"],
            registry=self.registry,
        )
        self.opp_rejected_total = Counter(
            "opportunities_rejected_total",
            "Opportunities rejected by risk",
            ["symbol", "reason"],
            registry=self.registry,
        )
        self.net_edge_bps_hist = Histogram(
            "net_edge_bps_histogram",
            "Distribution of detected net edge (bps)",
            ["symbol"],
            buckets=(-50, -20, -5, 0, 2, 5, 10, 20, 50, 100),
            registry=self.registry,
        )

        # Orders & execution
        self.orders_submitted_total = Counter(
            "orders_submitted_total",
            "Orders submitted",
            ["exchange", "side", "mode"],
            registry=self.registry,
        )
        self.orders_filled_total = Counter(
            "orders_filled_total",
            "Orders filled",
            ["exchange", "side"],
            registry=self.registry,
        )
        self.orders_rejected_total = Counter(
            "orders_rejected_total",
            "Orders rejected",
            ["exchange", "side"],
            registry=self.registry,
        )
        self.orders_cancelled_total = Counter(
            "orders_cancelled_total",
            "Orders cancelled",
            ["exchange", "side"],
            registry=self.registry,
        )
        self.partial_fill_total = Counter(
            "partial_fill_total", "Partial fill events", ["exchange"], registry=self.registry
        )
        self.repair_attempt_total = Counter("repair_attempt_total", "Repair attempts", registry=self.registry)
        self.repair_failed_total = Counter("repair_failed_total", "Repair failures", registry=self.registry)

        # Risk
        self.risk_reject_total = Counter(
            "risk_reject_total", "Risk rejections", ["reason"], registry=self.registry
        )
        self.circuit_breaker_trigger_total = Counter(
            "circuit_breaker_trigger_total",
            "Circuit breaker trips",
            registry=self.registry,
        )
        self.kill_switch_state = Gauge(
            "kill_switch_state",
            "Kill switch 1=on, 0=off",
            registry=self.registry,
        )

        # PnL — Gauges (not Counters) because realized PnL can be negative
        # and Counter.inc() rejects negative values. Bootstrap actually
        # increments these per-hedge after issue 6 fixed the un-incremented
        # paths.
        self.realized_pnl_quote_total = Gauge(
            "realized_pnl_quote_total",
            "Realized PnL (quote, cumulative, may be negative)",
            registry=self.registry,
        )
        self.gross_pnl_quote_total = Gauge(
            "gross_pnl_quote_total",
            "Gross PnL before fees (quote, cumulative, may be negative)",
            registry=self.registry,
        )
        self.fee_quote_total = Counter(
            "fee_quote_total",
            "Total fees paid (quote)",
            registry=self.registry,
        )

        # Async persistence pool — exposed by AsyncPersistence so an
        # operator can spot DB backpressure (writes piling up means
        # postgres is slow / unreachable).
        self.persistence_inflight = Gauge(
            "persistence_inflight",
            "DB write tasks currently in flight (opp/hedge persistence)",
            registry=self.registry,
        )
        self.persistence_inflight_high_water = Gauge(
            "persistence_inflight_high_water",
            "Peak in-flight persistence tasks since process start",
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)


_metrics: Metrics | None = None


def get_metrics() -> Metrics:
    global _metrics
    if _metrics is None:
        _metrics = Metrics()
    return _metrics
