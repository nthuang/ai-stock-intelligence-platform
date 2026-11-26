from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from backend import refresh


@pytest.fixture(autouse=True)
def open_session(monkeypatch):
    monkeypatch.setattr(refresh, 'market_state', lambda: {'refresh_window': True})


def test_closed_market_refresh_is_hourly(monkeypatch):
    monkeypatch.setattr(refresh, 'market_state', lambda: {'refresh_window': False})
    monkeypatch.setenv('REFRESH_INTERVAL_SECONDS', '60')
    assert refresh.MarketRefresh().interval == 3600


def test_live_refresh_caches_and_expires(monkeypatch):
    monkeypatch.setenv('DATA_MODE', 'live')
    monkeypatch.setenv('REFRESH_INTERVAL_SECONDS', '60')
    calls = []
    clock = [100.0]
    monkeypatch.setattr(refresh.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(refresh, 'fetch_and_store', lambda: calls.append(True))
    state = refresh.MarketRefresh()
    state.ensure()
    state.ensure()
    assert len(calls) == 1
    assert state.status()['last_successful_fetch'] is not None
    clock[0] = 161
    state.ensure()
    assert len(calls) == 2


def test_concurrent_requests_share_one_refresh(monkeypatch):
    monkeypatch.setenv('DATA_MODE', 'live')
    calls = []
    monkeypatch.setattr(refresh, 'fetch_and_store', lambda: calls.append(True))
    state = refresh.MarketRefresh()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: state.ensure(), range(8)))
    assert len(calls) == 1


def test_failure_returns_503_and_cools_down_without_leaking(monkeypatch):
    monkeypatch.setenv('DATA_MODE', 'live')
    calls = []
    clock = [100.0]
    monkeypatch.setattr(refresh.time, 'monotonic', lambda: clock[0])
    def fail():
        calls.append(True)
        raise RuntimeError('private connection details')
    monkeypatch.setattr(refresh, 'fetch_and_store', fail)
    state = refresh.MarketRefresh()
    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            state.ensure()
        assert exc.value.status_code == 503
        assert 'private' not in exc.value.detail
    assert len(calls) == 1
    assert state.last_success is None
    clock[0] = 131
    monkeypatch.setattr(refresh, 'fetch_and_store', lambda: None)
    state.ensure()
    assert state.error is None


def test_demo_does_not_fetch(monkeypatch):
    monkeypatch.setenv('DATA_MODE', 'demo')
    monkeypatch.setattr(refresh, 'fetch_and_store', lambda: pytest.fail('Unexpected provider call'))
    refresh.MarketRefresh().ensure()


def test_incomplete_universe_does_not_write(monkeypatch):
    import pandas as pd
    from scripts import fetch_data
    from backend import universe
    monkeypatch.setattr(universe, 'ingestion_tickers', lambda engine: ['AAPL', 'MSFT'])
    monkeypatch.setattr(fetch_data, 'TICKERS', ['AAPL', 'MSFT'])
    def fetch(ticker):
        if ticker == 'MSFT':
            raise ValueError('No data')
        return pd.DataFrame({'Date': [pd.Timestamp('2026-11-23')]})
    monkeypatch.setattr(fetch_data, 'fetch_ticker_data', fetch)
    monkeypatch.setattr(fetch_data, 'upsert_to_postgres', lambda _: pytest.fail('Partial write'))
    with pytest.raises(ValueError, match='No data'):
        refresh.fetch_and_store()


def test_benchmarks_ingested_separately_and_failure_does_not_block_equities(monkeypatch):
    import pandas as pd
    from scripts import fetch_data
    from backend import universe
    monkeypatch.setattr(universe, 'ingestion_tickers', lambda engine: ['AMD', 'SPY', 'QQQ'])
    calls, stored = [], []
    def fetch(ticker):
        calls.append(ticker)
        if ticker == 'QQQ':
            raise RuntimeError('Unavailable benchmark')
        return pd.DataFrame({'Ticker': [ticker], 'Date': [pd.Timestamp('2026-01-02')], 'Close': [100.0]})
    monkeypatch.setattr(fetch_data, 'fetch_ticker_data', fetch)
    monkeypatch.setattr(fetch_data, 'upsert_to_postgres', lambda frame: stored.extend(frame.Ticker))
    refresh.fetch_and_store()
    assert calls == ['AMD', 'SPY', 'QQQ']
    assert stored == ['AMD', 'SPY']
