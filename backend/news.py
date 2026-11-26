"""Bounded Tavily adapter. External text is evidence, never instructions."""
import logging
from contextvars import ContextVar
import math
import os
import re
import html
from xml.etree import ElementTree
import httpx
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from tavily import TavilyClient
from backend.market_calendar import session_close, session_bounds

logger = logging.getLogger(__name__)
MARKET_TIMEZONE = ZoneInfo('America/New_York')
COMPANIES = {"AAPL": "Apple", "AMZN": "Amazon", "MSFT": "Microsoft",
             "NVDA": "Nvidia", "TSLA": "Tesla"}
company_context = ContextVar('research_companies', default=COMPANIES)


def company_name(ticker):
    name = company_context.get().get(ticker, ticker)
    return re.sub(r',?\s+(?:incorporated|inc\.?|corporation|corp\.?|plc|ltd\.?|limited)\.?$', '', name, flags=re.I)


def company_pattern(ticker):
    # A / ON / IT are ordinary words: require explicit ticker notation for news.
    symbol = rf'\b{re.escape(ticker)}\b' if len(ticker) > 2 else rf'(?:\${re.escape(ticker)}\b|\({re.escape(ticker)}\))'
    return rf'(?:{symbol}|\b{re.escape(company_name(ticker))}\b)'


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
    # Exchange close (including early closes), capped at now.
    return min(session_close(market_date), datetime.now(timezone.utc))


def candidate_limit(display_limit: int) -> int:
    # A single bounded search needs room for candidates rejected by date policy.
    return min(20, max(10, display_limit * 2))


def session_article_date(value, market_date: str, cutoff: datetime | None = None) -> str | None:
    if not session_bounds(market_date):
        return None
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
            # A calendar date alone cannot establish publication before cutoff.
            return None
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
        query = '&'.join(part for part in parsed.query.split('&')
                         if not (unquote_plus(part.split('=', 1)[0]).lower().startswith('utm_')
                                 or unquote_plus(part.split('=', 1)[0]).lower() in {'.tsrc', 'gclid', 'fbclid'}))
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, query, ""))
    except ValueError:
        return None


def is_article(url, title):
    """Quote/overview pages can contain old headlines under a fresh page date."""
    return not (re.search(r'/quote(?:/|$)', urlsplit(url).path, re.I)
                or re.search(r'stock price, news, quote|stock quote.*history', title, re.I))


def article_relevance(ticker, title, content):
    """Transparent editorial ordering, not a probability or causal score."""
    company = company_pattern(ticker)
    if not re.search(company, title + ' ' + content, re.I):
        return {'priority': 0, 'coverage': 'unrelated'}
    if not re.search(company, title, re.I):
        return {'priority': 1, 'coverage': 'Market context'}
    if re.search(r'\b(buys?|sells?|bought|sold)\b', title, re.I) and re.search(r'\b(trust|fund|portfolio|holdings|shares|stake|position)\b', title, re.I):
        return {'priority': 1, 'coverage': 'Holdings report'}
    if re.search(r'^(does|should|can|is|will)\b|\b(analyst blog|should you|buy now|stocks? to buy|stocks? to sell|better than|worth buying|he.d buy|prediction|could soar|millionaire)\b', title, re.I):
        return {'priority': 2, 'coverage': 'Opinion / comparison'}
    if re.search(r'\b(earnings|revenue|guidance|forecast|launch\w*|unveil\w*|announc\w*|pact|agreement|deals?|CEO|CFO|leadership|acqui\w*|merger|lawsuit|regulat\w*|dividend|buyback|upgrade\w*|downgrade\w*)\b', title, re.I):
        return {'priority': 4, 'coverage': 'Company event'}
    return {'priority': 3, 'coverage': 'Company coverage'}


def rss_candidates(ticker):
    """One bounded public feed request, never a request to an article URL."""
    try:
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            with client.stream('GET', 'https://feeds.finance.yahoo.com/rss/2.0/headline',
                               params={'s': ticker, 'region': 'US', 'lang': 'en-US'},
                               headers={'User-Agent': 'Mozilla/5.0',
                                        'Accept': 'application/rss+xml, application/xml, text/xml'}) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 512_000:
                        raise ValueError('Feed exceeds size limit')
        if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
            raise ValueError('Unsupported XML declaration')
        root = ElementTree.fromstring(body)
        results = []
        for item in root.findall('./channel/item')[:50]:
            title = item.findtext('title') or ''
            content = html.unescape(re.sub(r'<[^>]+>', ' ', item.findtext('description') or ''))[:1500]
            # Company feeds can contain unrelated market headlines.
            if not re.search(company_pattern(ticker), title + ' ' + content, re.I):
                continue
            results.append({'title': title, 'content': content, 'url': item.findtext('link'),
                            'published_date': item.findtext('pubDate'), 'score': 0.5})
        return {'status': 'ok', 'results': results}
    except Exception as exc:
        logger.warning('Company news feed unavailable for %s: %s', ticker, type(exc).__name__)
        return {'status': 'error', 'results': []}


def search_stock_news(ticker: str, market_date: str, question: str | None = None,
                      *, cutoff: datetime | None = None) -> dict:
    ticker = ticker.upper().strip()
    if ticker not in company_context.get():
        raise ValueError(f"Unsupported ticker: {ticker}")
    if status := availability():
        return {"status": status, "articles": []}
    if not session_bounds(market_date):
        return {"status": "no_session", "articles": []}
    max_results = setting("NEWS_MAX_RESULTS", 5, 1, 20)
    start_date = date.fromisoformat(market_date)
    cutoff = min(cutoff, analysis_cutoff(market_date)) if cutoff else analysis_cutoff(market_date)
    intent = normalize_intent(question)
    query = f"{company_name(ticker)} {ticker} stock news"
    if intent != 'market catalysts':
        query += ' ' + intent
    try:
        response = TavilyClient(api_key=os.environ["TAVILY_API_KEY"]).search(
            query=query,
            topic="news", search_depth="basic", start_date=start_date.isoformat(),
            end_date=(start_date + timedelta(days=1)).isoformat(), max_results=candidate_limit(max_results),
            include_answer=False, include_raw_content=False, auto_parameters=False,
            include_published_date=True, filter_by_published_date=True,
            timeout=setting("NEWS_TIMEOUT_SECONDS", 15, 1, 30),
        )
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            return {"status": "malformed_response", "articles": []}
    except Exception as exc:
        logger.warning("News retrieval failed for %s: %s", ticker, type(exc).__name__)
        return {"status": "error", "articles": []}

    articles, seen = [], set()
    excluded = dict.fromkeys(("invalid_record", "unsafe_url", "unknown_timestamp", "non_article", "unrelated",
                              "outside_session", "after_cutoff", "duplicate"), 0)
    candidates = [(result, 'tavily') for result in response['results']]
    fallback_status = 'not_needed'
    index = 0
    while index < len(candidates) or (not articles and fallback_status == 'not_needed'):
        if index == len(candidates):
            fallback = rss_candidates(ticker)
            fallback_status = fallback['status']
            candidates.extend((result, 'yahoo_rss') for result in fallback['results'])
            if index == len(candidates):
                break
        result, provider = candidates[index]
        index += 1
        if not isinstance(result, dict):
            excluded["invalid_record"] += 1
            continue
        url = safe_url(result.get("url"))
        if not url:
            excluded["unsafe_url"] += 1
            continue
        if not is_article(url, str(result.get('title') or '')):
            excluded['non_article'] += 1
            continue
        published = publication_time(result.get("published_date"))
        if not published:
            excluded["unknown_timestamp"] += 1
            continue
        news_date = published.astimezone(MARKET_TIMEZONE).date().isoformat()
        if news_date != market_date:
            excluded["outside_session"] += 1
            continue
        if published > cutoff:
            excluded["after_cutoff"] += 1
            continue
        title, content = result.get("title"), result.get("content")
        if not isinstance(title, str) or not title.strip() or not isinstance(content, str):
            excluded["invalid_record"] += 1
            continue
        relevance = article_relevance(ticker, title, content)
        if not relevance['priority']:
            excluded['unrelated'] += 1
            continue
        if url in seen:
            excluded["duplicate"] += 1
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
                         "retrieval_provider": provider,
                         "coverage": relevance['coverage'], "editorial_priority": relevance['priority'],
                         "content": content[:1500], "relevance_score": score})
    articles.sort(key=lambda article: (article['editorial_priority'], article['relevance_score'],
                                      publication_time(article['published_date'])), reverse=True)
    return {"status": "ok" if articles else "no_results", "articles": articles[:max_results],
            "diagnostics": {"candidate_count": len(candidates), "eligible_count": len(articles),
                            "returned_count": min(len(articles), max_results), "excluded": excluded,
                            "rss_fallback": fallback_status}}
