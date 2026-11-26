"""Single-user monitored equities; benchmarks never enter the alert universe."""
from datetime import datetime, timezone
import os
import re

from fastapi import HTTPException
from sqlalchemy import select

from backend.models import WatchlistEntry, NewsUsage
from backend.news_cache import exclusive

DEFAULTS = {'AAPL': 'Apple', 'AMZN': 'Amazon', 'MSFT': 'Microsoft', 'NVDA': 'Nvidia', 'TSLA': 'Tesla'}
BENCHMARKS = {'SPY': 'S&P 500 ETF proxy', 'QQQ': 'Nasdaq-100 ETF proxy'}


def limit():
    try:
        return max(5, min(20, int(os.getenv('WATCHLIST_MAX_SIZE', '10'))))
    except ValueError:
        return 10


def entries(engine):
    with engine.connect() as connection:
        rows = connection.execute(select(WatchlistEntry.__table__)).mappings().all()
    # Compatibility for an initialized database before its first seed.
    if not rows:
        return [{'ticker': ticker, 'name': name, 'active': True} for ticker, name in DEFAULTS.items()]
    return [dict(row) for row in rows if row['active']]


def names(engine):
    return {row['ticker']: row['name'] for row in entries(engine)}


def missing_at(engine, market_date):
    from backend.models import StockPrice
    with engine.connect() as connection:
        configured = connection.execute(select(WatchlistEntry.ticker).where(WatchlistEntry.active.is_(True))).scalars().all()
        present = connection.execute(select(StockPrice.ticker).where(StockPrice.date == market_date)).scalars().all()
    return sorted(set(configured) - set(present))


def seed(engine):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if engine.dialect.name == 'postgresql' else sqlite_insert
    with engine.begin() as connection:
        connection.execute(insert(WatchlistEntry).values([
            {'ticker': ticker, 'name': name, 'active': True} for ticker, name in DEFAULTS.items()
        ]).on_conflict_do_nothing(index_elements=['ticker']))


def ingestion_tickers(engine):
    return [*names(engine), *BENCHMARKS]


def validate_symbol(value):
    ticker = value.strip().upper()
    if not re.fullmatch(r'[A-Z]{1,5}(?:-[A-Z])?', ticker) or ticker in BENCHMARKS:
        raise HTTPException(422, 'Use a US equity symbol; SPY and QQQ are separate benchmarks')
    return ticker


def validate_provider(ticker):
    import yfinance as yf
    try:
        quotes = yf.Search(ticker, max_results=5, news_count=0, lists_count=0,
                           recommended=0, timeout=10, raise_errors=True).quotes
    except Exception:
        raise HTTPException(503, 'Symbol validation provider unavailable; retry later') from None
    quote = next((q for q in quotes if q.get('symbol') == ticker and q.get('quoteType') == 'EQUITY'
                  and q.get('exchange') in {'NYQ', 'NMS', 'NGM', 'NCM', 'ASE', 'PCX', 'BTS'}), None)
    if not quote:
        raise HTTPException(422, 'No supported US-listed equity found for this symbol')
    return str(quote.get('shortname') or quote.get('longname') or ticker)[:160]


def change(engine, ticker, *, remove=False):
    """Serialize admissions across workers and reserve a daily provider-attempt budget."""
    ticker = validate_symbol(ticker)
    if os.getenv('DATA_MODE', 'live') != 'live':
        raise HTTPException(403, 'Watchlist changes are disabled in the offline demo')
    seed(engine)
    with exclusive(engine, 'watchlist-mutation') as connection:
        if connection is None:
            raise HTTPException(409, 'Another watchlist update is in progress')
        current = names(engine)
        table = WatchlistEntry.__table__
        if remove:
            if ticker not in current:
                raise HTTPException(404, 'Ticker is not monitored')
            if len(current) == 1:
                raise HTTPException(409, 'Keep at least one monitored equity')
            connection.execute(table.update().where(table.c.ticker == ticker).values(active=False))
            connection.commit()
            return {'ticker': ticker, 'active': False}
        if ticker in current:
            raise HTTPException(409, 'Ticker is already monitored')
        if len(current) >= limit():
            raise HTTPException(409, 'Watchlist size limit reached')
        usage = NewsUsage.__table__
        period = datetime.now(timezone.utc).strftime('watch:%Y-%m-%d')
        count = connection.scalar(select(usage.c.searches).where(usage.c.period == period)) or 0
        if count >= 20:
            raise HTTPException(429, 'Daily symbol-validation limit reached (20 attempts, UTC)')
        if count:
            connection.execute(usage.update().where(usage.c.period == period).values(searches=count + 1))
        else:
            connection.execute(usage.insert().values(period=period, searches=1))
        connection.commit()  # Failed provider attempts still consume this budget.
        name = validate_provider(ticker)
        from scripts.fetch_data import fetch_ticker_data, add_indicators, upsert_to_postgres
        try:
            frame = fetch_ticker_data(ticker)
            if frame.empty:
                raise ValueError('Empty history')
            upsert_to_postgres(add_indicators(frame), target_engine=engine)
        except Exception:
            raise HTTPException(503, 'Historical ingestion failed; ticker was not activated') from None
        existing = connection.scalar(select(table.c.ticker).where(table.c.ticker == ticker))
        if existing:
            connection.execute(table.update().where(table.c.ticker == ticker).values(name=name, active=True))
        else:
            connection.execute(table.insert().values(ticker=ticker, name=name, active=True))
        connection.commit()
        return {'ticker': ticker, 'name': name, 'active': True}
