"""Persistent news budgets and caches using the application's existing engine."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from threading import Lock
import time

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from backend import news
from backend.models import NewsCache, NewsUsage

_locks = [Lock() for _ in range(64)]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@contextmanager
def exclusive(engine, name: str):
    """Nonblocking PostgreSQL session lock; local equivalent for SQLite tests.

    A connection retains the lock across committed budget reservations. Crashes
    release the lock, while charged attempts remain in the database.
    """
    key = int(digest(name)[:15], 16)
    lock = _locks[key % len(_locks)]
    if not lock.acquire(blocking=False):
        yield None
        return
    try:
        with engine.connect() as connection:
            acquired = True
            if engine.dialect.name == "postgresql":
                acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
                connection.commit()
            try:
                yield connection if acquired else None
            finally:
                connection.rollback()
                if acquired and engine.dialect.name == "postgresql":
                    try:
                        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                        connection.commit()
                    except SQLAlchemyError:
                        connection.invalidate()
    finally:
        lock.release()


def read_cache(connection, table, key, now):
    row = connection.execute(select(table).where(table.c.key == key)).mappings().first()
    return row["payload"] if row and row["expires_at"] > now else None


def write_cache(connection, table, key, payload, expires):
    connection.execute(table.delete().where(table.c.key == key))
    connection.execute(table.insert().values(key=key, payload=payload, expires_at=expires))
    connection.commit()


def get_news(engine, ticker, market_date, question=None):
    if status := news.availability():
        return {"status": status, "articles": [], "cache_hit": False}
    display_limit = news.setting("NEWS_MAX_RESULTS", 5, 1, 20)
    key = digest({"version": 9, "date_policy": "same-session-exchange-close", "ticker": ticker.upper(), "date": market_date,
                  "intent": news.normalize_intent(question), "depth": "basic",
                  "results": display_limit, "candidate_limit": news.candidate_limit(display_limit)})
    table = NewsCache.__table__
    try:
        with engine.connect() as connection:
            cached = read_cache(connection, table, key, time.time())
        if cached:
            return {**cached, "cache_hit": True}
        with exclusive(engine, "news-budget") as connection:
            if connection is None:
                return {"status": "busy", "articles": [], "cache_hit": False}
            cached = read_cache(connection, table, key, time.time())
            if cached:
                return {**cached, "cache_hit": True}
            now = datetime.now(timezone.utc)
            limits = [(now.strftime("day:%Y-%m-%d"), news.setting("NEWS_DAILY_SEARCH_LIMIT", 20)),
                      (now.strftime("month:%Y-%m"), news.setting("NEWS_MONTHLY_SEARCH_LIMIT", 500, 0, 900))]
            usage = NewsUsage.__table__
            counts = {period: connection.scalar(select(usage.c.searches).where(usage.c.period == period)) or 0
                      for period, _ in limits}
            for period, limit in limits:
                if counts[period] >= limit:
                    return {"status": "daily_limit" if period.startswith("day:") else "monthly_limit",
                            "articles": [], "cache_hit": False}
            for period, _ in limits:
                if counts[period]:
                    connection.execute(usage.update().where(usage.c.period == period).values(searches=counts[period] + 1))
                else:
                    connection.execute(usage.insert().values(period=period, searches=1))
            connection.commit()
            result = news.search_stock_news(ticker, market_date, question)
            retrieved_at = datetime.now(timezone.utc)
            result["retrieved_at"] = retrieved_at.isoformat()
            ttl = news.setting("NEWS_CACHE_TTL_SECONDS", 21600, 1)
            if result["status"] not in {"ok", "no_results"}:
                ttl = min(ttl, 60)  # Cooldown, with no automatic credit-consuming retry.
            elif result["status"] == "no_results" or retrieved_at < news.session_close(market_date):
                # New reports can become eligible while the session develops.
                # Only a later explicit request retries, using the same budgets.
                ttl = min(ttl, 900)
            expires = time.time() + ttl
            result['cache_expires_at'] = datetime.fromtimestamp(expires, timezone.utc).isoformat()
            write_cache(connection, table, key, result, expires)
            return {**result, "cache_hit": False}
    except SQLAlchemyError:
        # Fail closed: unavailable quota storage must never permit unmetered calls.
        return {"status": "cache_unavailable", "articles": [], "cache_hit": False}
