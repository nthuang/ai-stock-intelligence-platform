"""Bounded Tavily adapter. External text is evidence, never instructions."""
import logging
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from tavily import TavilyClient

logger = logging.getLogger(__name__)
MARKET_TIMEZONE = ZoneInfo('America/New_York')
COMPANIES = {"AAPL": "Apple", "AMZN": "Amazon", "MSFT": "Microsoft",
             "NVDA": "Nvidia", "TSLA": "Tesla"}


def setting(name: str, default: int, minimum: int = 0, maximum: int = 1000000) -> int:
    try:
        return max(minimum, min(int(os.getenv(name, str(default))), maximum))
    except ValueError:
        return default


def availability() -> str | None:
    if os.getenv("DATA_MODE", "live") != "live":
        return "offline"
    if os.getenv("NEWS_ENABLED", "false").lower() != "true":
        return "disabled"
    if not os.getenv("TAVILY_API_KEY"):
        return "missing_api_key"
    return None


def normalize_intent(question: str | None) -> str:
    # Share equivalent searches without passing user instructions to Tavily.
    words = (question or "").lower()
    return next((intent for intent in ("earnings", "dividend", "acquisition", "lawsuit")
                 if intent in words), "market catalysts")


def analysis_cutoff(market_date: str) -> datetime:
    # Regular-session close, capped at now for an incomplete current session.
    # Daily bars do not contain an exchange calendar for special early closes.
    end = datetime.combine(date.fromisoformat(market_date), time(16), MARKET_TIMEZONE).astimezone(timezone.utc)
    return min(end, datetime.now(timezone.utc))


def session_article_date(value, market_date: str, cutoff: datetime | None = None) -> str | None:
    published = publication_time(value)
    boundary = min(cutoff, analysis_cutoff(market_date)) if cutoff else analysis_cutoff(market_date)
    if not published or published > boundary:
        return None
    session_date = published.astimezone(MARKET_TIMEZONE).date().isoformat()
    return session_date if session_date == market_date else None


def publication_time(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def safe_url(value) -> str | None:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))
    except ValueError:
        return None


def search_stock_news(ticker: str, market_date: str, question: str | None = None,
                      *, cutoff: datetime | None = None) -> dict:
    ticker = ticker.upper().strip()
    if ticker not in COMPANIES:
        raise ValueError(f"Unsupported ticker: {ticker}")
    if status := availability():
        return {"status": status, "articles": []}
    max_results = setting("NEWS_MAX_RESULTS", 5, 1, 20)
    start_date = date.fromisoformat(market_date)
    cutoff = min(cutoff, analysis_cutoff(market_date)) if cutoff else analysis_cutoff(market_date)
    try:
        response = TavilyClient(api_key=os.environ["TAVILY_API_KEY"]).search(
            query=f"{COMPANIES[ticker]} ({ticker}) stock financial news {normalize_intent(question)} {market_date}",
            topic="news", search_depth="basic", start_date=start_date.isoformat(),
            end_date=(date.fromisoformat(market_date) + timedelta(days=1)).isoformat(), max_results=max_results,
            include_answer=False, include_raw_content=False, auto_parameters=False,
            timeout=setting("NEWS_TIMEOUT_SECONDS", 15, 1, 30),
        )
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            return {"status": "malformed_response", "articles": []}
    except Exception as exc:
        logger.warning("News retrieval failed for %s: %s", ticker, type(exc).__name__)
        return {"status": "error", "articles": []}

    articles, seen = [], set()
    for result in response["results"]:
        if not isinstance(result, dict):
            continue
        url = safe_url(result.get("url"))
        news_date = session_article_date(result.get("published_date"), market_date, cutoff)
        title, content = result.get("title"), result.get("content")
        if not url or url in seen or not news_date:
            continue
        if not isinstance(title, str) or not title.strip() or not isinstance(content, str):
            continue
        try:
            score = float(result.get("score") or 0)
            if not math.isfinite(score):
                score = 0.0
        except (ValueError, TypeError):
            score = 0.0
        seen.add(url)
        articles.append({"ticker": ticker, "title": title[:300], "url": url,
                         "publisher": urlsplit(url).netloc, "published_date": result["published_date"],
                         "news_date": news_date, "stock_date": market_date,
                         "timezone": str(MARKET_TIMEZONE),
                         "content": content[:1500], "relevance_score": score})
    articles.sort(key=lambda article: article["relevance_score"], reverse=True)
    return {"status": "ok" if articles else "no_results", "articles": articles[:max_results]}
