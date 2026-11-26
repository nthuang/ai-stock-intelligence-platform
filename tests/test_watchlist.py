from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select

from backend import universe, news, research
from backend.models import Base, WatchlistEntry, NewsUsage
from scripts import fetch_data


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'watchlist.db'))
    Base.metadata.create_all(engine)
    universe.seed(engine)
    monkeypatch.setenv('DATA_MODE', 'live')
    yield engine
    engine.dispose()


def fake_history(monkeypatch):
    validate = Mock(return_value='Advanced Micro Devices')
    fetch = Mock(return_value=pd.DataFrame({'Date': [pd.Timestamp('2026-01-02')], 'Ticker': ['AMD'], 'Close': [100.0]}))
    store = Mock()
    monkeypatch.setattr(universe, 'validate_provider', validate)
    monkeypatch.setattr(fetch_data, 'fetch_ticker_data', fetch)
    monkeypatch.setattr(fetch_data, 'upsert_to_postgres', store)
    return validate, fetch, store


def test_persist_add_remove_and_seed_does_not_reactivate(database, monkeypatch):
    validate, fetch, store = fake_history(monkeypatch)
    assert universe.change(database, ' amd ')['ticker'] == 'AMD'
    validate.assert_called_once_with('AMD')
    fetch.assert_called_once_with('AMD')
    assert 'MA_30' in store.call_args.args[0]
    database.dispose()
    assert universe.names(database)['AMD'] == 'Advanced Micro Devices'
    universe.change(database, 'AAPL', remove=True)
    universe.seed(database)
    assert 'AAPL' not in universe.names(database)
    with database.connect() as connection:
        assert connection.scalar(select(WatchlistEntry.active).where(WatchlistEntry.ticker == 'AAPL')) is False


@pytest.mark.parametrize('ticker', ['SPY', 'QQQ', 'AAPL;DROP TABLE', '^GSPC', 'EURUSD=X', ''])
def test_invalid_equity_never_calls_provider(database, monkeypatch, ticker):
    validate, fetch, _ = fake_history(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        universe.change(database, ticker)
    assert exc.value.status_code == 422
    validate.assert_not_called()
    fetch.assert_not_called()


def test_duplicate_and_size_limits_do_not_call_provider(database, monkeypatch):
    validate, _, _ = fake_history(monkeypatch)
    with pytest.raises(HTTPException) as duplicate:
        universe.change(database, 'aapl')
    assert duplicate.value.status_code == 409
    monkeypatch.setenv('WATCHLIST_MAX_SIZE', '5')
    with pytest.raises(HTTPException) as full:
        universe.change(database, 'AMD')
    assert full.value.status_code == 409
    validate.assert_not_called()


def test_failure_is_not_activated_and_attempt_is_persistently_reserved(database, monkeypatch):
    _, fetch, _ = fake_history(monkeypatch)
    fetch.side_effect = RuntimeError('private provider details')
    with pytest.raises(HTTPException) as exc:
        universe.change(database, 'AMD')
    assert exc.value.status_code == 503 and 'private' not in exc.value.detail
    assert 'AMD' not in universe.names(database)
    with database.begin() as connection:
        assert connection.scalar(select(NewsUsage.searches)) == 1
        connection.execute(NewsUsage.__table__.update().values(searches=20))
    with pytest.raises(HTTPException) as exc:
        universe.change(database, 'AMD')
    assert exc.value.status_code == 429
    assert fetch.call_count == 1


def test_provider_validation_rejects_etfs_non_us_and_unavailable(monkeypatch):
    import yfinance as yf
    search = Mock(return_value=SimpleNamespace(quotes=[{'symbol': 'AMD', 'quoteType': 'EQUITY', 'exchange': 'NMS', 'shortname': 'AMD'}]))
    monkeypatch.setattr(yf, 'Search', search)
    assert universe.validate_provider('AMD') == 'AMD'
    assert search.call_args.kwargs['timeout'] == 10
    for quote in [{'symbol': 'AMD', 'quoteType': 'ETF', 'exchange': 'PCX'}, {'symbol': 'AMD', 'quoteType': 'EQUITY', 'exchange': 'LSE'}, {}]:
        search.return_value = SimpleNamespace(quotes=[quote])
        with pytest.raises(HTTPException) as exc:
            universe.validate_provider('AMD')
        assert exc.value.status_code == 422
    search.side_effect = RuntimeError('provider error')
    with pytest.raises(HTTPException) as exc:
        universe.validate_provider('AMD')
    assert exc.value.status_code == 503


def test_dynamic_company_news_context_is_isolated(database, monkeypatch):
    with database.begin() as connection:
        connection.execute(WatchlistEntry.__table__.insert().values(ticker='AMD', name='Advanced Micro Devices', active=True))
    seen = []
    def lookup(*args, **kwargs):
        seen.append(news.company_name('AMD'))
        assert news.article_relevance('AMD', 'Advanced Micro Devices reports earnings', '')['priority'] == 4
        return {'status': 'no_results', 'articles': [], 'cache_hit': False}
    monkeypatch.setattr(research, 'get_news', lookup)
    row = {'Ticker': 'AMD', 'Date': '2026-01-02', 'Observed At': '2026-01-02T21:30:00Z',
           'Close': 100.0, 'Open': 99.0, 'Daily Return %': 1, 'Signal': 'NORMAL'}
    result = research.research(database, [row], 'Why did AMD rise?')
    assert seen == ['Advanced Micro Devices']
    assert result['news_status'] == 'no_results'
    assert 'AMD' not in news.company_context.get()
