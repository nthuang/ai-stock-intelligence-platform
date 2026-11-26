from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from backend import main
from backend.models import Base, StockPrice


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('NOTIFICATION_API_KEY', 'test-notification-key')
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    with engine.begin() as connection:
        connection.execute(StockPrice.__table__.insert(), [
            dict(date=datetime(2026, 1, 1), ticker="AAPL", open=100, high=101, low=99,
                 close=100, volume=1000, daily_return_pct=None, signal=None),
            dict(date=datetime(2026, 1, 2), ticker="AAPL", open=100, high=105, low=99,
                 close=104, volume=1100, daily_return_pct=4, signal="BIG_GAIN"),
            dict(date=datetime(2026, 1, 2), ticker="MSFT", open=200, high=201, low=190,
                 close=192, volume=900, daily_return_pct=-4, signal="BIG_DROP"),
        ])
    with TestClient(main.app, headers={'X-Notification-Key': 'test-notification-key'}) as test_client:
        yield test_client
    engine.dispose()


def test_root_endpoint(client):
    assert client.get("/").json()["message"] == "AI Stock Intelligence API is running"


def test_watchlist_and_benchmarks_are_separate_and_removed_history_is_retained(client, monkeypatch):
    from backend import universe
    universe.seed(main.engine)
    assert len(client.get('/watchlist').json()['stocks']) == 5
    assert all(item['status'] == 'unavailable' for item in client.get('/benchmarks').json()['benchmarks'])
    assert client.post('/watchlist', json={'ticker': 'AMD'}).status_code == 403
    monkeypatch.setenv('WATCHLIST_API_KEY', 'watch-test')
    # Demo never enables provider-backed mutations even with a valid key.
    assert client.post('/watchlist', json={'ticker': 'AMD'}, headers={'X-Watchlist-Key': 'watch-test'}).status_code == 403
    monkeypatch.setenv('DATA_MODE', 'live')
    monkeypatch.setattr(main.market_refresh, 'ensure', lambda: None)
    response = client.delete('/watchlist/AAPL', headers={'X-Watchlist-Key': 'watch-test'})
    assert response.status_code == 200
    assert client.get('/stocks?ticker=AAPL').json() == []
    assert all(row['Ticker'] != 'AAPL' for row in client.get('/latest').json())
    with main.engine.connect() as connection:
        assert connection.execute(StockPrice.__table__.select().where(StockPrice.ticker == 'AAPL')).first() is not None
    with main.engine.begin() as connection:
        connection.execute(StockPrice.__table__.insert().values(date=datetime(2026, 1, 2), ticker='SPY',
            open=100, high=105, low=99, close=104, volume=1000, daily_return_pct=4, signal='BIG_GAIN'))
    assert all(row['Ticker'] != 'SPY' for row in client.get('/signals/latest').json())
    assert client.get('/benchmarks').json()['benchmarks'][0]['latest']['Ticker'] == 'SPY'
    assert client.get('/ai-summary/latest').json()['quantitative_context']['benchmark'] == 'SPY'


def test_analytics_and_quantitative_ask_do_not_use_news_or_llm(client, monkeypatch):
    from backend import research
    monkeypatch.setattr(research, 'get_news', lambda *a, **k: pytest.fail('Unnecessary news call'))
    monkeypatch.setattr(research.ai_summary, '_get_client', lambda: pytest.fail('Unnecessary LLM call'))
    result = client.get('/analytics?benchmark=QQQ&sessions=5&window=20')
    assert result.status_code == 200
    assert result.json()['benchmark_status'] == 'insufficient_history'
    assert client.get('/analytics?benchmark=BAD').status_code == 422
    assert client.get('/analytics?sessions=9999').status_code == 422
    answer = client.post('/ask', json={'question': 'Which stock has the highest beta relative to SPY?'}).json()
    assert 'unavailable' in answer['answer']
    assert answer['news_status'] == 'not_requested'
    assert answer['llm_source'] == 'rule'


def test_new_stock_is_available_through_existing_api_contracts(client, monkeypatch):
    import pandas as pd
    from backend import universe
    from scripts import fetch_data
    monkeypatch.setenv('DATA_MODE', 'live')
    monkeypatch.setenv('WATCHLIST_API_KEY', 'watch-test')
    monkeypatch.setattr(main.market_refresh, 'ensure', lambda: None)
    monkeypatch.setattr(universe, 'validate_provider', lambda ticker: 'Advanced Micro Devices')
    frame = pd.DataFrame({'Date': [pd.Timestamp('2026-01-02')], 'Ticker': ['AMD'], 'Close': [100.0]})
    monkeypatch.setattr(fetch_data, 'fetch_ticker_data', lambda ticker: frame.copy())
    def store(frame, target_engine):
        with target_engine.begin() as connection:
            connection.execute(StockPrice.__table__.insert().values(date=datetime(2026, 1, 2), ticker='AMD',
                open=99, high=101, low=98, close=100, volume=1000, daily_return_pct=1, signal='NORMAL'))
    monkeypatch.setattr(fetch_data, 'upsert_to_postgres', store)
    assert client.post('/watchlist', json={'ticker': 'AMD'}, headers={'X-Watchlist-Key': 'watch-test'}).status_code == 201
    assert client.get('/stocks?ticker=AMD').json()[0]['Ticker'] == 'AMD'
    assert 'AMD' in client.get('/tickers').json()['tickers']
    answer = client.post('/ask', json={'question': 'What is AMD closing price?'}).json()
    assert '$100.00' in answer['answer'] and 'Unsupported' not in answer['answer']
    news = client.post('/stock-news', json={'ticker': 'AMD', 'date': '2026-01-02'}).json()
    assert news['ticker'] == 'AMD' and news['news_status'] == 'snapshot_unavailable'


def test_configured_missing_equity_prevents_fresh_snapshot(client, monkeypatch):
    from backend import universe
    universe.seed(main.engine)
    monkeypatch.setattr(main, 'data_age', lambda value: (0, False))
    assert client.get('/health').json()['stale'] is True
    metadata = main.research_metadata(main.latest_records())
    assert 'NVDA' in metadata['data_freshness']['missing_monitored_tickers']
    assert metadata['stale'] is True


def test_status_never_refreshes_providers(client, monkeypatch):
    monkeypatch.setattr(main.market_refresh, 'ensure', lambda: pytest.fail('Status refreshed provider'))
    result = client.get('/system/status')
    assert result.status_code == 200
    body = result.json()
    assert body['news_budget_utc']['daily']['used'] == 0
    assert body['market']['exchange'] == 'NASDAQ'
    assert body['delivery_records'] == {}
    assert 'counters' in body['process']


def test_observation_only_refresh_reuses_summary(client):
    from backend.models import StockObservation
    assert client.get('/ai-summary/latest').json()['cache_hit'] is False
    with main.engine.begin() as connection:
        connection.execute(StockObservation.__table__.insert(), [
            {'date': datetime(2026, 1, 2), 'ticker': ticker,
             'observed_at': datetime.fromisoformat('2026-01-02T21:30:00+00:00')}
            for ticker in ('AAPL', 'MSFT')])
    assert client.get('/ai-summary/latest').json()['cache_hit'] is True


def test_notifications_cannot_send_previous_session_on_weekend(client, monkeypatch):
    monkeypatch.setattr(main, 'data_age', lambda value: (1, False))
    monkeypatch.setattr(main, 'market_state', lambda: {'market_date': '2026-01-03'})
    result = client.post('/notifications/claim', json={
        'date': '2026-01-02', 'recipient': 'test@example.com'}).json()
    assert result == {'allowed': False, 'reason': 'ineligible_snapshot'}


def test_null_indicators_and_sql_filters(client):
    response = client.get("/stocks?ticker=aapl&start=2026-01-01&end=2026-01-01")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["MA_30"] is None
    assert response.json()[0]["Daily Return %"] is None


def test_pagination_and_validation(client):
    assert client.get("/stocks?limit=1").json()[0]["Ticker"] == "AAPL"
    assert client.get("/stocks?limit=1&offset=1").json()[0]["Ticker"] == "MSFT"
    assert client.get("/stocks?limit=1001").status_code == 422
    assert client.get("/stocks?offset=-1").status_code == 422
    assert client.get("/stocks?start=2026-02-01&end=2026-01-01").status_code == 422
    assert client.get("/stocks?ticker=UNKNOWN").json() == []


def test_latest_signals_movers_and_tickers(client):
    assert len(client.get("/latest").json()) == 2
    assert len(client.get("/signals").json()) == 2
    assert len(client.get("/signals/latest").json()) == 2
    assert client.get("/tickers").json() == {"tickers": ["AAPL", "MSFT"]}
    movers = client.get("/top-movers").json()
    assert movers["top_gainers"][0]["Ticker"] == "AAPL"
    assert movers["top_losers"][0]["Ticker"] == "MSFT"


def test_stale_data_and_summary_fallback(client):
    health = client.get("/health").json()
    assert health["rows"] == 3
    assert health["stale"] is True
    response = client.get("/ai-summary/latest")
    assert response.status_code == 200
    assert response.json()["stale"] is True
    assert response.json()["llm_source"] == "fallback"


def test_empty_database(client):
    with main.engine.begin() as connection:
        connection.execute(StockPrice.__table__.delete())
    assert client.get("/stocks").json() == []
    assert client.get("/signals").json() == []
    assert client.get("/latest").status_code == 404
    assert client.get("/signals/latest").status_code == 404
    assert client.get("/health").json()["status"] == "degraded"


def test_no_signals_is_consistent_list(client):
    with main.engine.begin() as connection:
        connection.execute(StockPrice.__table__.update().values(signal="NORMAL"))
    assert client.get("/signals/latest").json() == []
    assert client.get("/ai-summary/latest").json()["llm_source"] == "fallback"


def test_database_error_does_not_leak_details(client, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError("password=secret", {}, Exception("private host"))
    monkeypatch.setattr(main.engine, "connect", fail)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database temporarily unavailable"}


def test_response_schemas_are_documented(client):
    schema = client.get("/openapi.json").json()
    assert "StockRecord" in schema["components"]["schemas"]
    assert "SummaryResponse" in schema["components"]["schemas"]


def test_normal_market_still_gets_ai_summary(client, monkeypatch):
    captured = []
    def summarize(rows):
        captured.extend(rows)
        return {'summary': 'Modest movement across the snapshot.', 'source': 'groq', 'model': 'test'}
    monkeypatch.setattr(main, 'generate_summary', summarize)
    with main.engine.begin() as connection:
        connection.execute(StockPrice.__table__.update().values(signal='NORMAL'))
    result = client.get('/ai-summary/latest').json()
    assert result['llm_source'] == 'groq'
    assert result['signals'] == []
    assert len(captured) == 2


def test_live_provider_failure_does_not_serve_stale_prices(client, monkeypatch):
    from fastapi import HTTPException
    def fail():
        raise HTTPException(status_code=503, detail='Live refresh unavailable')
    monkeypatch.setattr(main.market_refresh, 'ensure', fail)
    assert client.get('/latest').status_code == 503
    assert client.get('/ai-summary/latest').status_code == 503


def test_ask_quantitative_does_not_request_news(client, monkeypatch):
    from backend import research
    def forbidden(*args, **kwargs):
        raise AssertionError('Quantitative question consumed news credits')
    monkeypatch.setattr(research, 'get_news', forbidden)
    result = client.post('/ask', json={'question': "What is AAPL's latest closing price?"}).json()
    assert '$104.00' in result['answer']
    assert 'MSFT:' not in result['answer']
    assert result['news_status'] == 'not_requested'
    assert result['llm_source'] == 'rule'
    assert result['sources'] == []
    assert result['date'] == '2026-01-02'
    assert 'AAPL' in client.post('/ask', json={'question': 'Which stock performed best today?'}).json()['answer']
    assert 'insufficient history' in client.post('/ask', json={'question': "What is Apple's 30-day moving average?"}).json()['answer']
    assert client.post('/ask', json={'question': ' '}).status_code == 422


def test_ask_news_and_summary_compatibility(client):
    result = client.post('/ask', json={'question': 'Why did Microsoft decline?'}).json()
    assert result['news_status'] == 'offline'
    assert 'unverified' in result['answer']
    assert result['performance'][0]['Ticker'] == 'MSFT'
    summary = client.get('/ai-summary/latest').json()
    assert {'date', 'summary', 'llm_source', 'model', 'stale', 'signals', 'provider_error', 'last_successful_fetch'} <= summary.keys()
    assert len(summary['performance']) == 2
    assert summary['analysis_cutoff'].startswith('2026-01-02')
    assert summary['generated_at']
    assert client.get('/ai-summary/latest').json()['cache_hit'] is True


def test_summary_cache_changes_with_market_data(client, monkeypatch):
    calls = []
    def summarize(rows):
        calls.append(rows)
        return {'summary': 'Observed movements.', 'source': 'groq', 'model': 'fake'}
    monkeypatch.setattr(main, 'generate_summary', summarize)
    assert client.get('/ai-summary/latest').json()['cache_hit'] is False
    assert client.get('/ai-summary/latest').json()['cache_hit'] is True
    with main.engine.begin() as connection:
        connection.execute(StockPrice.__table__.update().where(StockPrice.ticker == 'AAPL').values(close=105))
    assert client.get('/ai-summary/latest').json()['cache_hit'] is False
    assert len(calls) == 2


def test_stale_notifications_are_never_claimed(client):
    result = client.post('/notifications/claim', json={'date': '2026-01-02', 'recipient': 'test@example.com'}).json()
    assert result == {'allowed': False, 'reason': 'ineligible_snapshot'}


def test_legacy_live_prices_never_inherit_timestamp_from_newer_fetch(client, monkeypatch):
    monkeypatch.setenv('DATA_MODE', 'live')
    monkeypatch.setattr(main.market_refresh, 'ensure', lambda: None)
    monkeypatch.setattr(main.market_refresh, 'last_success', '2026-01-03T23:00:00Z')
    response = client.post('/stock-news', json={'ticker': 'AAPL', 'date': '2026-01-02'}).json()
    assert response['news_status'] == 'snapshot_unavailable'
    assert response['sources'] == [] and response['analysis_cutoff'] is None
    assert response['data_freshness']['session_status'] == 'unknown'
    assert client.get('/health').json()['latest_snapshot_observed_at'] is None


def test_delivery_http_flow_waits_for_close_and_deduplicates(client, monkeypatch):
    from backend.models import StockObservation
    monkeypatch.setattr(main, 'data_age', lambda value: (0, False))
    monkeypatch.setattr(main, 'market_state', lambda: {'market_date': '2026-01-02'})
    with main.engine.begin() as connection:
        connection.execute(StockObservation.__table__.insert(), [
            {'date': datetime(2026, 1, 2), 'ticker': ticker,
             'observed_at': datetime.fromisoformat('2026-01-02T15:00:00+00:00')}
            for ticker in ('AAPL', 'MSFT')])
    payload = {'date': '2026-01-02', 'recipient': 'test@example.com'}
    assert client.post('/notifications/claim', json=payload).json()['allowed'] is False
    with main.engine.begin() as connection:
        connection.execute(StockObservation.__table__.update().values(
            observed_at=datetime.fromisoformat('2026-01-02T21:30:00+00:00')))
    claim = client.post('/notifications/claim', json=payload).json()
    assert claim['allowed'] and 'Daily Market Intelligence' in claim['html']
    assert client.post('/notifications/claim', json=payload).json()['reason'] == 'pending'
    acknowledgment = {'key': claim['key'], 'token': claim['token']}
    assert client.post('/notifications/complete', json=acknowledgment).json() == {'recorded': True}
    assert client.post('/notifications/complete', json=acknowledgment).json() == {'recorded': True}
    assert client.post('/notifications/claim', json=payload).json()['reason'] == 'already_sent'


def test_ask_returns_retrieved_sources_and_cited_explanation(client, monkeypatch):
    import json
    from types import SimpleNamespace
    from backend import research
    article = {'ticker': 'AAPL', 'title': 'Apple earnings', 'url': 'https://example.com/apple',
               'publisher': 'example.com', 'published_date': '2026-01-02T12:00:00Z',
               'content': 'Apple reported earnings.', 'relevance_score': .9}
    monkeypatch.setattr(research, 'get_news', lambda *args, **kwargs: {'status': 'ok', 'articles': [article], 'cache_hit': False})
    content = json.dumps({'overview': 'Apple moved amid earnings reports.',
                          'explanations': [{'source_id': 'S1', 'topic': 'earnings', 'evidence_quote': article['content']}]})
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
    monkeypatch.setattr(research.ai_summary, '_get_client', lambda: fake)
    result = client.post('/ask', json={'question': 'Why did Apple rise?'}).json()
    assert result['news_status'] == 'ok'
    assert result['sources'][0]['url'] == article['url']
    assert '[S1]' in result['answer'] and '$104.00' in result['answer']
    assert result['explanations'][0]['reported_event'] == article['title']
    assert 'content' not in result['sources'][0]


def test_unsupported_ticker_uses_no_news(client, monkeypatch):
    from backend import research
    def forbidden(*args, **kwargs):
        raise AssertionError('Unsupported ticker should not search news')
    monkeypatch.setattr(research, 'get_news', forbidden)
    result = client.post('/ask', json={'question': 'Why did GOOG fall?'}).json()
    assert 'Unsupported ticker' in result['answer']
    assert result['sources'] == []


def test_summary_interprets_breadth_instead_of_repeating_quotes(client):
    result = client.get('/ai-summary/latest').json()
    assert 'close $' not in result['summary']
    assert '1 of 2' in result['summary']
    assert '8.00 percentage points' in result['summary']
    assert result['analysis']['metrics']['advancers'] == 1
    assert result['analysis']['metrics']['equal_weight_return_pct'] == 0


def test_market_question_and_company_typo_are_routed(client):
    from backend.research import route_question
    selected, wants_news = route_question('why nvdia up', [{'Ticker': 'NVDA'}, {'Ticker': 'AAPL'}])
    assert selected == [{'Ticker': 'NVDA'}]
    assert wants_news
    answer = client.post('/ask', json={'question': 'how did the market do?'}).json()
    assert '1 of 2' in answer['answer']
    assert 'close $' not in answer['answer']


def test_question_with_wrong_direction_is_corrected(client):
    result = client.post('/ask', json={'question': 'Why did Microsoft rise?'}).json()
    assert 'MSFT fell on 2026-01-02' in result['premise_note']
    assert result['answer'].startswith(result['premise_note'])


def test_stock_news_always_searches_selected_company_and_displayed_date(client, monkeypatch):
    from backend import research
    calls = []
    def lookup(engine, ticker, market_date, question):
        calls.append((ticker, market_date, question))
        return {'status': 'no_results', 'articles': [], 'cache_hit': False}
    monkeypatch.setattr(research, 'get_news', lookup)
    result = client.post('/stock-news', json={'ticker': 'aapl', 'date': '2026-01-01'})
    assert result.status_code == 200
    payload = result.json()
    assert payload['ticker'] == 'AAPL'
    assert payload['date'] == '2026-01-01'
    assert payload['performance'][0]['Close'] == 100
    assert len(payload['performance']) == 1
    assert [(ticker, date) for ticker, date, _ in calls] == [('AAPL', '2026-01-01')]
    assert 'news_retrieval' in payload['tools_used']
    assert payload['news_status'] == 'no_results'


def test_stock_news_does_not_substitute_another_date_or_company(client, monkeypatch):
    from backend import research
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid selection should not consume news credits')
    monkeypatch.setattr(research, 'get_news', forbidden)
    assert client.post('/stock-news', json={'ticker': 'MSFT', 'date': '2026-01-01'}).status_code == 404
    assert client.post('/stock-news', json={'ticker': 'GOOG', 'date': '2026-01-02'}).status_code == 422
    assert client.post('/stock-news', json={'ticker': 'AAPL', 'date': 'invalid'}).status_code == 422


def test_stock_news_keeps_dated_links_when_commentary_fails(client, monkeypatch):
    from backend import research
    article = {'ticker': 'AAPL', 'title': 'Apple earnings', 'url': 'https://example.com/apple',
               'publisher': 'example.com', 'published_date': '2026-01-02T12:00:00Z',
               'content': 'Apple reported earnings.', 'relevance_score': .9}
    monkeypatch.setattr(research, 'get_news', lambda *args, **kwargs: {
        'status': 'ok', 'articles': [article], 'cache_hit': True})
    result = client.post('/stock-news', json={'ticker': 'AAPL', 'date': '2026-01-02'}).json()
    assert result['llm_source'] == 'fallback'
    assert result['news_status'] == 'ok'
    assert result['sources'][0]['url'] == article['url']
    assert result['sources'][0]['news_date'] == result['sources'][0]['stock_date'] == result['date']
    assert result['explanations'] == []
