"""Issue 4 — paper-fill engine realism."""

from datetime import datetime, timezone
from decimal import Decimal

from app.common.enums import OrderType, Side
from app.execution.paper_fill_engine import PaperFillConfig, PaperFillEngine
from app.marketdata.orderbook_manager import OrderBookManager
from app.models.order import OrderIntent
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot


def _book(asks: list[tuple[float, float]], bids: list[tuple[float, float]] | None = None):
    return OrderBookSnapshot(
        exchange="x",
        symbol="BTC/USDT",
        bids=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in (bids or [(0, 0)])],
        asks=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in asks],
        ts_local=datetime.now(timezone.utc),
    )


def _intent(
    side: Side,
    amount: Decimal,
    price: Decimal | None,
    order_type: OrderType = OrderType.IOC_LIMIT,
) -> OrderIntent:
    return OrderIntent(
        hedge_group_id="hg1",
        client_order_id="co1",
        exchange="x",
        symbol="BTC/USDT",
        side=side,
        order_type=order_type,
        amount=amount,
        price=price,
    )


def test_buy_ioc_stops_at_limit_price():
    """Pre-fix the engine walked every level. Post-fix it stops as soon
    as a level breaches the limit, which is the IOC semantics."""
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 0.2), (105, 1.0)]))
    eng = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    intent = _intent(Side.BUY, Decimal("1.0"), Decimal("100"))
    state = eng.simulate(intent)
    assert state.filled == Decimal("0.2")
    assert state.avg_fill_price == Decimal("100")


def test_buy_ioc_with_buffer_walks_to_buffer_limit():
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 0.2), (102, 0.5), (110, 10)]))
    eng = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    # 3 bp buffer above 100 = 100.03 → only first level stops walking at 102
    intent = _intent(Side.BUY, Decimal("1.0"), Decimal("100.03"))
    state = eng.simulate(intent)
    assert state.filled == Decimal("0.2")


def test_sell_ioc_stops_below_limit_price():
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 1)], bids=[(99, 0.5), (95, 10)]))
    eng = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    intent = _intent(Side.SELL, Decimal("1.0"), Decimal("99"))
    state = eng.simulate(intent)
    assert state.filled == Decimal("0.5")
    assert state.avg_fill_price == Decimal("99")


def test_market_order_ignores_limit_price():
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 0.2), (200, 10)]))
    eng = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    intent = _intent(Side.BUY, Decimal("1.0"), Decimal("100"), order_type=OrderType.MARKET)
    state = eng.simulate(intent)
    # Market order walks the whole book
    assert state.filled == Decimal("1.0")
    # 0.2*100 + 0.8*200 = 180 → avg = 180
    assert state.avg_fill_price == Decimal("180")


def test_fok_rejects_when_full_amount_not_available():
    """FOK is all-or-nothing. If the book can only fill 0.2 of a 1.0 order
    within the limit price, a real venue rejects the order entirely; the
    paper engine must do the same. Pre-fix it returned a 0.2 partial fill
    identical to IOC, which made paper hedges spuriously trigger the
    repair path while live would have cleanly rejected and moved on."""
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 0.2), (105, 1.0)]))
    eng = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    intent = _intent(Side.BUY, Decimal("1.0"), Decimal("100"), order_type=OrderType.FOK_LIMIT)
    state = eng.simulate(intent)
    assert state.filled == Decimal("0")
    assert state.remaining == Decimal("1.0")
    assert state.avg_fill_price is None


def test_fok_fills_when_full_amount_available():
    """Counter-test: FOK should fill normally when the book has enough."""
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 1.0), (105, 5)]))
    eng = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    intent = _intent(Side.BUY, Decimal("1.0"), Decimal("100"), order_type=OrderType.FOK_LIMIT)
    state = eng.simulate(intent)
    assert state.filled == Decimal("1.0")
    assert state.avg_fill_price == Decimal("100")


def test_ioc_still_partial_fills_when_full_amount_not_available():
    """Sanity counter-test: IOC must still partially fill — only FOK is
    all-or-nothing. Same book as the FOK rejection test."""
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 0.2), (105, 1.0)]))
    eng = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    intent = _intent(Side.BUY, Decimal("1.0"), Decimal("100"), order_type=OrderType.IOC_LIMIT)
    state = eng.simulate(intent)
    assert state.filled == Decimal("0.2")


def test_book_drift_during_latency_makes_paper_match_live():
    """Audit's headline test: scanner saw 100, but during paper_fill_latency_ms
    the book drifted. Paper fill must reflect the drifted book, not the
    original snapshot. We simulate this by mutating the book between
    intent creation and simulate() — the engine reads the current book
    each time."""
    books = OrderBookManager(max_stale_ms=999999)
    books.update(_book(asks=[(100, 1)]))  # what the scanner saw
    eng = PaperFillEngine(books, cfg=PaperFillConfig(partial_fill_probability=0.0))
    # Book drifts between scan and fill: best ask now 101.
    books.update(_book(asks=[(101, 1)]))
    intent = _intent(Side.BUY, Decimal("1.0"), Decimal("100"))
    state = eng.simulate(intent)
    # Limit was 100, current book starts at 101 → no fill.
    assert state.filled == Decimal("0")
