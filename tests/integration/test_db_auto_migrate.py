"""Auto-migration of missing columns on existing tables.

Pre-fix: ``Base.metadata.create_all()`` only creates missing *tables*, so
adding a new column to an ORM model silently broke every SELECT on an
upgraded production DB. This test exercises the helper directly against
a synchronous in-memory SQLite engine.
"""

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect

from app.storage.db import Database


def test_add_missing_columns_alters_existing_table(monkeypatch):
    engine = create_engine("sqlite:///:memory:")

    legacy_meta = MetaData()
    Table(
        "opportunities",
        legacy_meta,
        Column("id", String(64), primary_key=True),
        Column("symbol", String(32)),
    )
    legacy_meta.create_all(engine)

    new_meta = MetaData()
    Table(
        "opportunities",
        new_meta,
        Column("id", String(64), primary_key=True),
        Column("symbol", String(32)),
        Column("buy_book_age_ms", Integer, nullable=True),
        Column("sell_book_age_ms", Integer, nullable=True),
    )
    # Substitute Base.metadata that the helper reads from.
    monkeypatch.setattr("app.storage.db.Base.metadata", new_meta)

    with engine.begin() as conn:
        Database._add_missing_columns(conn)

    with engine.connect() as conn:
        cols = {col["name"] for col in inspect(conn).get_columns("opportunities")}
    assert "buy_book_age_ms" in cols
    assert "sell_book_age_ms" in cols


def test_add_missing_columns_no_op_when_table_missing(monkeypatch):
    """If the table doesn't exist yet, ``create_all`` will create it; the
    helper must not fail trying to alter a non-existent table."""
    engine = create_engine("sqlite:///:memory:")

    new_meta = MetaData()
    Table(
        "trade_quality",
        new_meta,
        Column("hedge_group_id", String(64), primary_key=True),
    )
    monkeypatch.setattr("app.storage.db.Base.metadata", new_meta)

    with engine.begin() as conn:
        Database._add_missing_columns(conn)  # should not raise

    with engine.connect() as conn:
        names = inspect(conn).get_table_names()
    assert "trade_quality" not in names  # helper does not create tables
