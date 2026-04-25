"""ORM models. Keep field names aligned with the domain models for clarity."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    best_ask: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    mid_price: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    top_bid_size: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    top_ask_size: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    raw_depth: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts_exchange: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ts_local: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    buy_exchange: Mapped[str] = mapped_column(String(32))
    sell_exchange: Mapped[str] = mapped_column(String(32))
    buy_price: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    sell_price: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    gross_spread_bps: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    buy_fee_bps: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    sell_fee_bps: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    buffer_bps: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    net_edge_bps: Mapped[Decimal] = mapped_column(Numeric(20, 6), index=True)
    max_tradable_size: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    expected_profit_quote: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Issue 5 — orderbook snapshot age at detection time. Nullable so older
    # rows / opportunities created in tests stay valid.
    buy_book_age_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sell_book_age_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class HedgeGroup(Base):
    __tablename__ = "hedge_groups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    buy_exchange: Mapped[str] = mapped_column(String(32))
    sell_exchange: Mapped[str] = mapped_column(String(32))
    target_amount: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    state: Mapped[str] = mapped_column(String(32), index=True)
    executed_buy_amount: Mapped[Decimal] = mapped_column(Numeric(32, 12), default=Decimal(0))
    executed_sell_amount: Mapped[Decimal] = mapped_column(Numeric(32, 12), default=Decimal(0))
    net_position_base: Mapped[Decimal] = mapped_column(Numeric(32, 12), default=Decimal(0))
    expected_profit_quote: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    realized_profit_quote: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hedge_group_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16))
    is_repair: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_price: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    status: Mapped[str] = mapped_column(String(32), index=True)
    filled_amount: Mapped[Decimal] = mapped_column(Numeric(32, 12), default=Decimal(0))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    fee_asset: Mapped[str | None] = mapped_column(String(16), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BalanceRow(Base):
    __tablename__ = "balance_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    asset: Mapped[str] = mapped_column(String(16), index=True)
    free: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    locked: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    total: Mapped[Decimal] = mapped_column(Numeric(32, 12))
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    component: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)


class ConfigAudit(Base):
    __tablename__ = "config_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(64), index=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(64), default="api")
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ConfigSnapshotRow(Base):
    """Single-row table holding the latest persisted runtime config.

    A pkey of ``"current"`` is the canonical row. ``payload`` is a JSON map
    of ``{settings_attr: stringified_value}``. On boot we load this row and
    apply every field to the in-memory ``Settings`` object so operator
    edits via /config and /strategies/{id}/configure survive container
    restarts. Older snapshots (audit) live in ``ConfigAudit``.
    """

    __tablename__ = "config_snapshots"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TradeQuality(Base):
    """Per-hedge "Arbitrage Trade Quality Report" row.

    Issue 6 — surfaces expected-vs-realized so we can answer questions
    like "how much of our edge is fees vs slippage vs repair losses".

    One row per finished hedge group. Written from the bootstrap right
    after ``HedgeCoordinator.execute`` returns; never mutated again.
    """

    __tablename__ = "trade_quality"

    hedge_group_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    buy_exchange: Mapped[str] = mapped_column(String(32))
    sell_exchange: Mapped[str] = mapped_column(String(32))
    final_state: Mapped[str] = mapped_column(String(32), index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    expected_edge_bps: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    expected_profit_quote: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    # Re-evaluated at HedgeCoordinator entry on the *approved* size.
    expected_profit_quote_at_approved_size: Mapped[Decimal | None] = mapped_column(
        Numeric(32, 12), nullable=True
    )
    expected_edge_bps_at_approved_size: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)

    buy_expected_vwap: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    sell_expected_vwap: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    buy_actual_vwap: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    sell_actual_vwap: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)

    executed_buy_amount: Mapped[Decimal] = mapped_column(Numeric(32, 12), default=Decimal(0))
    executed_sell_amount: Mapped[Decimal] = mapped_column(Numeric(32, 12), default=Decimal(0))

    actual_fee_quote: Mapped[Decimal] = mapped_column(Numeric(32, 12), default=Decimal(0))
    actual_slippage_quote: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)
    repair_cost_quote: Mapped[Decimal] = mapped_column(Numeric(32, 12), default=Decimal(0))
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    net_realized_pnl_quote: Mapped[Decimal | None] = mapped_column(Numeric(32, 12), nullable=True)

    latency_ms_signal_to_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms_order_to_fill: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buy_book_age_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sell_book_age_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )


Index("ix_opportunities_detected_at_net_edge", Opportunity.detected_at, Opportunity.net_edge_bps)
Index("ix_orders_created_at", OrderRow.created_at)
Index("ix_trade_quality_finished_at", TradeQuality.finished_at)
