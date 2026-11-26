"""Real production upsert against an explicitly configured disposable PostgreSQL database."""
import os
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select, text

from backend.models import Base, StockPrice, StockObservation
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
        frame['Observed At'] = pd.Timestamp('2026-01-01T15:00:00Z')
        fetch_data.upsert_to_postgres(frame)
        fetch_data.upsert_to_postgres(frame)
        frame["Close"] = 102.0
        frame['Observed At'] = pd.Timestamp('2026-01-01T16:00:00Z')
        fetch_data.upsert_to_postgres(frame)
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(StockPrice)) == 1
            row = connection.execute(select(StockPrice.__table__)).mappings().one()
            assert row["close"] == 102.0
            assert row["ma_30"] is None
            observed = connection.execute(select(StockObservation.__table__)).mappings().one()
            assert observed['ticker'] == 'TEST'
            assert observed['observed_at'].hour == 16
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()


@pytest.fixture
def postgres_news_engine():
    if not os.getenv('TEST_DATABASE_URL', '').startswith('postgresql'):
        pytest.skip('Set TEST_DATABASE_URL to a disposable PostgreSQL database')
    base = create_engine(os.environ['TEST_DATABASE_URL'])
    schema = 'test_' + uuid4().hex
    with base.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(os.environ['TEST_DATABASE_URL'], connect_args={'options': f'-csearch_path={schema}'})
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()


def test_postgres_news_budget_cache_and_external_lock(postgres_news_engine, monkeypatch):
    from unittest.mock import Mock
    from backend import news, news_cache
    engine = postgres_news_engine
    monkeypatch.setenv('DATA_MODE', 'live')
    monkeypatch.setenv('NEWS_ENABLED', 'true')
    monkeypatch.setenv('TAVILY_API_KEY', 'test')
    monkeypatch.setenv('NEWS_DAILY_SEARCH_LIMIT', '1')
    monkeypatch.setenv('NEWS_MONTHLY_SEARCH_LIMIT', '500')
    search = Mock(return_value={'status': 'no_results', 'articles': []})
    monkeypatch.setattr(news, 'search_stock_news', search)
    # A separately held server lock simulates another process, bypassing local locks.
    key = int(news_cache.digest('news-budget')[:15], 16)
    with engine.connect() as other:
        other.execute(text('SELECT pg_advisory_lock(:key)'), {'key': key})
        other.commit()
        try:
            assert news_cache.get_news(engine, 'NVDA', '2026-01-02')['status'] == 'busy'
            search.assert_not_called()
        finally:
            other.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
            other.commit()
    assert news_cache.get_news(engine, 'NVDA', '2026-01-02')['status'] == 'no_results'
    engine.dispose()  # Restart the connection pool; persisted data survives.
    assert news_cache.get_news(engine, 'NVDA', '2026-01-02')['cache_hit']
    assert news_cache.get_news(engine, 'AAPL', '2026-01-02')['status'] == 'daily_limit'
    assert search.call_count == 1


def test_postgres_delivery_deduplication(postgres_news_engine):
    from backend.notifications import claim_delivery, complete_delivery
    engine = postgres_news_engine
    claim = claim_delivery(engine, '2026-01-02', 'test@example.com')
    assert claim['allowed']
    assert complete_delivery(engine, claim['key'], claim['token'])
    engine.dispose()
    assert claim_delivery(engine, '2026-01-02', 'test@example.com')['reason'] == 'already_sent'
