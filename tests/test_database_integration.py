"""Real production upsert against an explicitly configured disposable PostgreSQL database."""
import os
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select, text

from backend.models import Base, StockPrice
from scripts import fetch_data

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
                    reason="Set TEST_DATABASE_URL to a disposable PostgreSQL database")
def test_postgres_upsert_is_idempotent_and_updates(monkeypatch):
    # Unique schema isolates this test from existing tables and concurrent runs.
    base = create_engine(os.environ["TEST_DATABASE_URL"])
    schema = "test_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(os.environ["TEST_DATABASE_URL"],
                           connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        monkeypatch.setattr(fetch_data, "engine", engine)
        frame = pd.DataFrame({
            "Ticker": ["TEST"], "Date": [pd.Timestamp("2026-01-01")],
            "Open": [100.0], "High": [105.0], "Low": [99.0],
            "Close": [101.0], "Volume": [1000],
        })
        frame = fetch_data.add_indicators(frame)
        fetch_data.upsert_to_postgres(frame)
        fetch_data.upsert_to_postgres(frame)
        frame["Close"] = 102.0
        fetch_data.upsert_to_postgres(frame)
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(StockPrice)) == 1
            row = connection.execute(select(StockPrice.__table__)).mappings().one()
            assert row["close"] == 102.0
            assert row["ma_30"] is None
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()
