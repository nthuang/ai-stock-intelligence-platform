from unittest.mock import patch

from backend.news import search_stock_news


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
                "published_date": "2026-11-24",
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