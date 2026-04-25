"""Internal enums shared across the system."""

from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    DRY_RUN = "dry-run"
    PAPER_TRADE = "paper-trade"
    LIVE = "live"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"
    IOC_LIMIT = "ioc_limit"
    FOK_LIMIT = "fok_limit"


class OrderStatus(str, Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class HedgeState(str, Enum):
    NEW = "new"
    PLANNING = "planning"
    SUBMITTING = "submitting"
    HEDGING = "hedging"
    COMPLETED = "completed"
    FAILED_NEEDS_REPAIR = "failed_needs_repair"
    REPAIRING = "repairing"
    ABORTED = "aborted"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RejectReason(str, Enum):
    KILL_SWITCH = "kill_switch"
    CIRCUIT_BREAKER = "circuit_breaker"
    MARKET_DATA_STALE = "market_data_stale"
    BELOW_MIN_EDGE = "below_min_edge"
    BELOW_MIN_PROFIT = "below_min_profit"
    BELOW_MIN_SIZE = "below_min_size"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    MAX_EXPOSURE = "max_exposure"
    TOO_MANY_OPEN_HEDGES = "too_many_open_hedges"
    COOLDOWN_ACTIVE = "cooldown_active"
    MODE_DISALLOWED = "mode_disallowed"
    SYMBOL_NOT_WHITELISTED = "symbol_not_whitelisted"
    EXCHANGE_UNHEALTHY = "exchange_unhealthy"
    BOOK_TOO_OLD = "book_too_old"
    SIGNAL_TOO_OLD = "signal_too_old"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    CONSECUTIVE_LOSS_LIMIT = "consecutive_loss_limit"
    OTHER = "other"
