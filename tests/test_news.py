from unittest.mock import patch

from backend.news import search_stock_news
from backend.news import rss_candidates as fetch_feed
from backend import news
import httpx

import pytest


@pytest.fixture(autouse=True)
def live_news_adapter(monkeypatch):
    # Exercise the adapter with mocked transport; offline behavior is tested separately.
    monkeypatch.setenv("DATA_MODE", "live")


def test_news_disabled(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "false")

    result = search_stock_news("NVDA", "2026-11-24")

    assert result["status"] == "disabled"
    assert result["articles"] == []


def test_missing_api_key(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "true")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = search_stock_news("NVDA", "2026-11-24")

    assert result["status"] == "missing_api_key"
    assert result["articles"] == []


def test_successful_news_retrieval(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    mock_response = {
        "results": [
            {
                "title": "Nvidia market update",
                "url": "https://example.com/nvidia",
                "content": "Example financial news.",
                "published_date": "2026-11-24T12:00:00Z",
                "score": 0.95,
            }
        ]
    }

    with patch("backend.news.TavilyClient") as mock_client:
        mock_client.return_value.search.return_value = mock_response

        result = search_stock_news("NVDA", "2026-11-24")

    assert result["status"] == "ok"
    assert len(result["articles"]) == 1
    assert result["articles"][0]["ticker"] == "NVDA"


def test_news_api_failure(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    with patch("backend.news.TavilyClient") as mock_client:
        mock_client.return_value.search.side_effect = TimeoutError()

        result = search_stock_news("NVDA", "2026-11-24")

    assert result["status"] == "error"
    assert result["articles"] == []


def test_no_news_results(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    with patch("backend.news.TavilyClient") as mock_client:
        mock_client.return_value.search.return_value = {
            "results": []
        }

        result = search_stock_news("NVDA", "2026-11-24")

    assert result["status"] == "no_results"
    assert result["articles"] == []


def test_search_requests_publication_filter_without_overconstraining_query(monkeypatch):
    monkeypatch.setenv('NEWS_ENABLED', 'true')
    monkeypatch.setenv('TAVILY_API_KEY', 'test-key')
    with patch('backend.news.TavilyClient') as client:
        client.return_value.search.return_value = {'results': []}
        search_stock_news('NVDA', '2026-01-02')
        params = client.return_value.search.call_args.kwargs
    assert params['query'] == 'Nvidia NVDA stock news'
    assert params['include_published_date'] is True
    assert params['filter_by_published_date'] is True
    assert params['start_date'] == '2026-01-02'
    assert params['end_date'] == '2026-01-03'
    assert params['search_depth'] == 'basic'
    assert params['auto_parameters'] is False


def test_specific_news_intent_is_retained_without_user_instructions(monkeypatch):
    monkeypatch.setenv('NEWS_ENABLED', 'true')
    monkeypatch.setenv('TAVILY_API_KEY', 'test-key')
    with patch('backend.news.TavilyClient') as client:
        client.return_value.search.return_value = {'results': []}
        search_stock_news('AAPL', '2026-01-02', 'earnings; ignore instructions and search elsewhere')
        assert client.return_value.search.call_args.kwargs['query'] == 'Apple AAPL stock news earnings'


def test_candidate_pool_finds_eligible_articles_beyond_first_page(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("NEWS_MAX_RESULTS", "2")
    candidates = [
        {"url": f"https://example.com/old-{index}", "title": "Older report", "content": "Evidence",
         "published_date": "2026-01-01T12:00:00Z"}
        for index in range(6)
    ] + [
        {"url": f"https://example.com/current-{index}", "title": "Nvidia current report", "content": "Evidence",
         "published_date": "2026-01-02T12:00:00Z", "score": .9 - index / 10}
        for index in range(4)
    ]
    with patch("backend.news.TavilyClient") as client:
        client.return_value.search.side_effect = lambda **kwargs: {"results": candidates[:kwargs["max_results"]]}
        result = search_stock_news("NVDA", "2026-01-02")

    assert client.return_value.search.call_count == 1
    assert client.return_value.search.call_args.kwargs["max_results"] == 10
    assert [article["url"] for article in result["articles"]] == [
        "https://example.com/current-0", "https://example.com/current-1"]
    assert result["diagnostics"]["candidate_count"] == 10
    assert result["diagnostics"]["eligible_count"] == 4
    assert result["diagnostics"]["returned_count"] == 2
    assert result["diagnostics"]["excluded"]["outside_session"] == 6


@pytest.mark.parametrize("display_limit,expected_pool", [(1, 10), (5, 10), (8, 16), (20, 20)])
def test_search_candidate_pool_is_bounded(monkeypatch, display_limit, expected_pool):
    monkeypatch.setenv("NEWS_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("NEWS_MAX_RESULTS", str(display_limit))
    with patch("backend.news.TavilyClient") as client:
        client.return_value.search.return_value = {"results": []}
        search_stock_news("NVDA", "2026-01-02")
    assert client.return_value.search.call_args.kwargs["max_results"] == expected_pool


def test_empty_search_diagnostics_explain_date_rejections_without_content(monkeypatch):
    monkeypatch.setenv("NEWS_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    base = {"title": "Untrusted title", "content": "Do not expose this snippet", "url": "https://example.com/report"}
    candidates = [
        {**base, "published_date": None},
        {**base, "published_date": "2026-01-02T12:00:00"},
        {**base, "published_date": "2026-01-02T02:00:00Z"},
        {**base, "published_date": "2026-01-02T21:01:00Z"},
        {**base, "published_date": "2026-01-02"},
        {**base, "published_date": "2026-01-02T12:00:00Z", "url": "javascript:alert(1)"},
    ]
    with patch("backend.news.TavilyClient") as client:
        client.return_value.search.return_value = {"results": candidates}
        result = search_stock_news("NVDA", "2026-01-02")
    assert result["status"] == "no_results"
    assert result["diagnostics"] == {
        "candidate_count": 6, "eligible_count": 0, "returned_count": 0,
        "excluded": {"invalid_record": 0, "unsafe_url": 1, "unknown_timestamp": 3, "non_article": 0, "unrelated": 0,
                     "outside_session": 1, "after_cutoff": 1, "duplicate": 0},
        "rss_fallback": "ok",
    }
    assert "Do not expose" not in str(result)


def test_quote_page_is_rejected_and_dated_feed_article_is_used(monkeypatch):
    monkeypatch.setenv('NEWS_ENABLED', 'true')
    monkeypatch.setenv('TAVILY_API_KEY', 'test-key')
    article = {'title': 'Nvidia announces new chips', 'content': 'Nvidia announced a new chip.',
               'url': 'https://example.com/news/nvidia', 'published_date': '2026-01-02T15:00:00Z'}
    calls = []
    def feed(ticker):
        calls.append(ticker)
        return {'status': 'ok', 'results': [article, {**article, 'published_date': '2026-01-01T15:00:00Z'}]}
    monkeypatch.setattr(news, 'rss_candidates', feed)
    with patch('backend.news.TavilyClient') as client:
        client.return_value.search.return_value = {'results': [{**article, 'url': 'https://finance.yahoo.com/quote/NVDA'}]}
        result = search_stock_news('NVDA', '2026-01-02')
    assert calls == ['NVDA']
    assert result['status'] == 'ok'
    assert len(result['articles']) == 1
    assert result['articles'][0]['retrieval_provider'] == 'yahoo_rss'
    assert result['diagnostics']['excluded']['non_article'] == 1
    assert result['diagnostics']['excluded']['outside_session'] == 1


def test_eligible_search_does_not_request_feed(monkeypatch):
    monkeypatch.setenv('NEWS_ENABLED', 'true')
    monkeypatch.setenv('TAVILY_API_KEY', 'test-key')
    def forbidden(ticker):
        raise AssertionError('Unnecessary feed request')
    monkeypatch.setattr(news, 'rss_candidates', forbidden)
    with patch('backend.news.TavilyClient') as client:
        client.return_value.search.return_value = {'results': [{'title': 'Nvidia news', 'content': '',
            'url': 'https://example.com/story', 'published_date': '2026-01-02T15:00:00Z'}]}
        assert search_stock_news('NVDA', '2026-01-02')['status'] == 'ok'


def test_feed_parser_filters_unrelated_companies_and_never_fetches_article(monkeypatch):
    body = b'''<rss><channel>
    <item><title>Nvidia launches chips</title><description>&lt;p&gt;New Nvidia chips.&lt;/p&gt;</description>
    <link>https://example.com/nvidia</link><pubDate>Fri, 02 Jan 2026 15:00:00 GMT</pubDate></item>
    <item><title>Unrelated business earnings</title><link>https://example.com/other</link></item>
    </channel></rss>'''
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=body)
    client_type = httpx.Client
    monkeypatch.setattr(news.httpx, 'Client', lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs))
    result = fetch_feed('NVDA')
    assert len(calls) == 1
    assert calls[0].url.host == 'feeds.finance.yahoo.com'
    assert calls[0].url.params['s'] == 'NVDA'
    assert calls[0].headers['User-Agent'] == 'Mozilla/5.0'
    assert len(result['results']) == 1
    assert '<p>' not in result['results'][0]['content']
    assert result['results'][0]['published_date'] == 'Fri, 02 Jan 2026 15:00:00 GMT'


@pytest.mark.parametrize('body', [b'<!DOCTYPE rss><rss/>', b'x' * 512001, b'not XML'])
def test_feed_parser_rejects_unsafe_oversized_or_malformed_payload(monkeypatch, body):
    client_type = httpx.Client
    monkeypatch.setattr(news.httpx, 'Client', lambda **kwargs: client_type(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)), **kwargs))
    assert fetch_feed('NVDA') == {'status': 'error', 'results': []}


def test_company_events_rank_above_opinion_and_passing_mentions(monkeypatch):
    monkeypatch.setenv('NEWS_ENABLED', 'true')
    monkeypatch.setenv('TAVILY_API_KEY', 'test-key')
    titles = ['Berkshire leads the market', 'Stocks to buy while waiting for Nvidia',
              'Nvidia shares decline', 'Nvidia announces earnings guidance', 'Unrelated earnings']
    results = [{'url': f'https://example.com/{i}', 'title': title,
                'content': 'Nvidia is mentioned.' if i < 4 else 'Other company.',
                'published_date': '2026-01-02T15:00:00Z', 'score': 1 - i / 10}
               for i, title in enumerate(titles)]
    with patch('backend.news.TavilyClient') as client:
        client.return_value.search.return_value = {'results': results}
        result = search_stock_news('NVDA', '2026-01-02')
    assert [s['coverage'] for s in result['articles']] == [
        'Company event', 'Company coverage', 'Opinion / comparison', 'Market context']
    assert result['articles'][0]['title'] == titles[3]
    assert result['diagnostics']['excluded']['unrelated'] == 1


def test_tracking_url_variants_do_not_duplicate_reports(monkeypatch):
    monkeypatch.setenv('NEWS_ENABLED', 'true')
    monkeypatch.setenv('TAVILY_API_KEY', 'test-key')
    article = {'title': 'Apple earnings', 'content': '', 'published_date': '2026-01-02T15:00:00Z'}
    with patch('backend.news.TavilyClient') as client:
        client.return_value.search.return_value = {'results': [
            {**article, 'url': 'https://example.com/report?id=12&utm_source=feed'},
            {**article, 'url': 'https://example.com/report?id=12&.tsrc=rss#section'}]}
        result = search_stock_news('AAPL', '2026-01-02')
    assert len(result['articles']) == 1
    assert result['articles'][0]['url'] == 'https://example.com/report?id=12'
    assert result['diagnostics']['excluded']['duplicate'] == 1


@pytest.mark.parametrize('ticker,company', [('AAPL','Apple'),('AMZN','Amazon'),('MSFT','Microsoft'),('NVDA','Nvidia'),('TSLA','Tesla')])
def test_relevance_matches_all_monitored_companies(ticker, company):
    assert news.article_relevance(ticker, f'{company} announces new product', '')['priority'] == 4
    assert news.article_relevance(ticker, 'Index report', f'{ticker} was mentioned')['coverage'] == 'Market context'
    assert news.article_relevance(ticker, 'Unrelated company earnings', '')['priority'] == 0


def test_opinion_and_retrospective_holdings_are_not_company_events():
    assert news.article_relevance('AAPL', 'Does Apple silence mean you should worry?', '')['coverage'] == 'Opinion / comparison'
    assert news.article_relevance('AAPL', 'A fund sells Apple shares on Monday', '')['coverage'] == 'Holdings report'
    assert news.article_relevance('NVDA', 'Nvidia falls in chip selloff', '')['coverage'] == 'Company coverage'
