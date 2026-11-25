import logging
import os
from datetime import date, timedelta
from urllib.parse import urlparse

from tavily import TavilyClient

logger = logging.getLogger(__name__)

COMPANIES = {
    "AAPL": "Apple",
    "AMZN": "Amazon",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "TSLA": "Tesla",
}


def search_stock_news(
    ticker: str,
    market_date: str,
    question: str | None = None,
) -> dict:

    ticker = ticker.upper().strip()

    if ticker not in COMPANIES:
        raise ValueError(f"Unsupported ticker: {ticker}")

    if os.getenv("NEWS_ENABLED", "false").lower() != "true":
        return {"status": "disabled", "articles": []}

    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        return {"status": "missing_api_key", "articles": []}

    max_results = int(os.getenv("NEWS_MAX_RESULTS", "5"))
    lookback_days = int(os.getenv("NEWS_LOOKBACK_DAYS", "3"))

    max_results = max(1, min(max_results, 20))
    lookback_days = max(0, lookback_days)

    end_date = date.fromisoformat(market_date)
    start_date = end_date - timedelta(days=lookback_days)

    query = (
        f"{COMPANIES[ticker]} ({ticker}) stock price movement "
        f"financial news earnings market catalysts {market_date}"
    )

    if question:
        query += f" {question[:200]}"

    try:
        client = TavilyClient(api_key=api_key)

        response = client.search(
            query=query,
            topic="news",
            search_depth="basic",
            start_date=start_date.isoformat(),
            end_date=(end_date + timedelta(days=1)).isoformat(),
            max_results=max_results,
            include_answer=False,
            include_raw_content=False,
            auto_parameters=False,
            timeout=15,
        )

    except Exception as exc:
        logger.warning(
            "News retrieval failed for %s: %s",
            ticker,
            type(exc).__name__,
        )

        return {"status": "error", "articles": []}

    articles = []
    seen_urls = set()

    for result in response.get("results", []):

        url = result.get("url", "")
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            continue

        if not parsed.netloc or url in seen_urls:
            continue

        published_date = result.get("published_date")

        # Exclude articles with known dates outside the window.
        if published_date:
            try:
                published_day = date.fromisoformat(
                    published_date[:10]
                )

                if not start_date <= published_day <= end_date:
                    continue

            except (ValueError, TypeError):
                pass

        seen_urls.add(url)

        articles.append({
            "ticker": ticker,
            "title": result.get("title", ""),
            "url": url,
            "publisher": parsed.netloc,
            "published_date": published_date,
            "content": result.get("content", "")[:1500],
            "relevance_score": result.get("score") or 0.0,
        })

    articles.sort(
        key=lambda article: article["relevance_score"],
        reverse=True,
    )

    return {
        "status": "ok" if articles else "no_results",
        "articles": articles[:max_results],
    }