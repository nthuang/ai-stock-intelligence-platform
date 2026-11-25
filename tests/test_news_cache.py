from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select

from backend import news, news_cache
from backend.models import Base, NewsCache, NewsUsage


@pytest.fixture
def cache_engine(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'cache.db'))
    Base.metadata.create_all(engine)
    monkeypatch.setenv('DATA_MODE', 'live')
    monkeypatch.setenv('NEWS_ENABLED', 'true')
    monkeypatch.setenv('TAVILY_API_KEY', 'fake')
    monkeypatch.setenv('NEWS_DAILY_SEARCH_LIMIT', '20')
    monkeypatch.setenv('NEWS_MONTHLY_SEARCH_LIMIT', '500')
    yield engine
    engine.dispose()


def test_cache_reuse_expiration_and_parameters(cache_engine, monkeypatch):
    search = Mock(return_value={'status': 'ok', 'articles': []})
    monkeypatch.setattr(news, 'search_stock_news', search)
    first = news_cache.get_news(cache_engine, 'NVDA', '2026-01-02', 'Why did Nvidia fall?')
    assert not first['cache_hit']
    assert news_cache.get_news(cache_engine, 'NVDA', '2026-01-02', 'Explain NVDA movement')['cache_hit']
    assert search.call_count == 1
    with cache_engine.begin() as connection:
        connection.execute(NewsCache.__table__.update().values(expires_at=0))
    assert not news_cache.get_news(cache_engine, 'NVDA', '2026-01-02')['cache_hit']
    monkeypatch.setenv('NEWS_MAX_RESULTS', '3')
    news_cache.get_news(cache_engine, 'NVDA', '2026-01-02')
    assert search.call_count == 3
    # A fresh engine simulates a process restart: persistent cache is still usable.
    other = create_engine(cache_engine.url)
    try:
        assert news_cache.get_news(other, 'NVDA', '2026-01-02')['cache_hit']
    finally:
        other.dispose()


@pytest.mark.parametrize('limit,status', [('NEWS_DAILY_SEARCH_LIMIT', 'daily_limit'), ('NEWS_MONTHLY_SEARCH_LIMIT', 'monthly_limit')])
def test_usage_limit_persists_and_failed_calls_count(cache_engine, monkeypatch, limit, status):
    monkeypatch.setenv(limit, '1')
    search = Mock(return_value={'status': 'error', 'articles': []})
    monkeypatch.setattr(news, 'search_stock_news', search)
    assert news_cache.get_news(cache_engine, 'NVDA', '2026-01-02')['status'] == 'error'
    assert news_cache.get_news(cache_engine, 'NVDA', '2026-01-02')['cache_hit']
    assert news_cache.get_news(cache_engine, 'AAPL', '2026-01-02')['status'] == status
    with cache_engine.connect() as connection:
        assert connection.execute(select(NewsUsage.searches)).scalars().all() == [1, 1]
    assert search.call_count == 1


def test_concurrent_duplicates_are_not_searched(cache_engine, monkeypatch):
    entered, release = Event(), Event()
    def search(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {'status': 'no_results', 'articles': []}
    mock = Mock(side_effect=search)
    monkeypatch.setattr(news, 'search_stock_news', mock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(news_cache.get_news, cache_engine, 'NVDA', '2026-01-02')
        assert entered.wait(5)
        try:
            assert news_cache.get_news(cache_engine, 'NVDA', '2026-01-02')['status'] == 'busy'
        finally:
            release.set()
        assert first.result()['status'] == 'no_results'
    assert news_cache.get_news(cache_engine, 'NVDA', '2026-01-02')['cache_hit']
    assert mock.call_count == 1


def test_quota_storage_failure_fails_closed(cache_engine, monkeypatch):
    NewsCache.__table__.drop(cache_engine)
    search = Mock()
    monkeypatch.setattr(news, 'search_stock_news', search)
    assert news_cache.get_news(cache_engine, 'NVDA', '2026-01-02')['status'] == 'cache_unavailable'
    search.assert_not_called()


def test_offline_never_searches(cache_engine, monkeypatch):
    monkeypatch.setenv('DATA_MODE', 'demo')
    search = Mock()
    monkeypatch.setattr(news, 'TavilyClient', search)
    assert news.search_stock_news('NVDA', '2026-01-02')['status'] == 'offline'
    assert news_cache.get_news(cache_engine, 'NVDA', '2026-01-02')['status'] == 'offline'
    search.assert_not_called()


def test_filtering_malformed_duplicate_future_unknown_dates(cache_engine, monkeypatch):
    good = {'url': 'https://example.com/story', 'title': 'Report', 'content': 'Evidence', 'published_date': '2026-01-02T12:00:00Z', 'score': .9}
    results = [None, 'bad', good, {**good, 'url': good['url'] + '#fragment'},
               {**good, 'url': 'javascript:alert(1)'}, {**good, 'url': 34},
               {**good, 'url': 'https://example.com/future', 'published_date': '2026-01-03T12:00:00Z'},
               {**good, 'url': 'https://example.com/unknown', 'published_date': None},
               {**good, 'url': 'https://example.com/bad', 'published_date': 'garbage'},
               {**good, 'url': 'https://example.com/null', 'content': None},
               {**good, 'url': 'https://example.com/score', 'score': 'not-a-number'}]
    client = Mock()
    client.return_value.search.return_value = {'results': results}
    monkeypatch.setattr(news, 'TavilyClient', client)
    result = news.search_stock_news('NVDA', '2026-01-02')
    assert [x['url'] for x in result['articles']] == ['https://example.com/story', 'https://example.com/score']
    assert client.return_value.search.call_args.kwargs['search_depth'] == 'basic'
    assert client.return_value.search.call_args.kwargs['auto_parameters'] is False
    for response in (None, [], {'results': None}, {'results': 'bad'}):
        client.return_value.search.return_value = response
        assert news.search_stock_news('NVDA', '2026-01-02')['status'] == 'malformed_response'


def test_exact_cutoff_and_date_only_are_conservative(cache_engine, monkeypatch):
    client = Mock()
    client.return_value.search.return_value = {'results': [
        {'url': 'https://example.com/after', 'title': 'After', 'content': '', 'published_date': '2026-01-02T16:01:00Z'},
        {'url': 'https://example.com/unknown-time', 'title': 'Unknown time', 'content': '', 'published_date': '2026-01-02'},
    ]}
    monkeypatch.setattr(news, 'TavilyClient', client)
    assert news.search_stock_news('NVDA', '2026-01-02', cutoff=datetime(2026, 1, 2, 16, tzinfo=timezone.utc))['articles'] == []


def test_news_matches_session_day_and_precedes_close(cache_engine, monkeypatch):
    good = {'url': 'https://example.com/same-day', 'title': 'Report', 'content': 'Evidence',
            'published_date': '2026-01-02T20:59:00Z', 'score': .9}
    client = Mock()
    client.return_value.search.return_value = {'results': [good,
        {**good, 'url': 'https://example.com/yesterday', 'published_date': '2026-01-01T20:00:00Z'},
        {**good, 'url': 'https://example.com/utc-midnight', 'published_date': '2026-01-02T02:00:00Z'},
        {**good, 'url': 'https://example.com/after-close', 'published_date': '2026-01-02T21:01:00Z'},
    ]}
    monkeypatch.setattr(news, 'TavilyClient', client)
    result = news.search_stock_news('NVDA', '2026-01-02')
    assert [a['url'] for a in result['articles']] == [good['url']]
    assert result['articles'][0]['news_date'] == '2026-01-02'
    assert client.return_value.search.call_args.kwargs['start_date'] == '2026-01-02'
