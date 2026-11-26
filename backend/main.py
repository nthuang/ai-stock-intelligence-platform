"""Read-only analytics API with bounded SQL queries and explicit contracts."""
from datetime import date, datetime, timezone
import logging
import os
import re
from pathlib import Path
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.ai_summary import generate_summary
from backend.db import engine
from backend.models import StockPrice, StockObservation
from backend.refresh import market_refresh
from backend.schemas import AskRequest, AskResponse, HealthResponse, MoversResponse, StockRecord, SummaryResponse
from backend.research import research
from backend.news import COMPANIES, analysis_cutoff, publication_time
from backend.market_calendar import session_bounds, market_state
from backend.operations import snapshot as operational_snapshot
from backend.access import access_control
from backend.notifications import claim_delivery, complete_delivery, render_report
from backend.schemas import AlertClaimRequest, AlertCompleteRequest, StockNewsRequest, StockNewsResponse
from backend.schemas import WatchlistRequest
from backend import universe
from backend import quantitative
from typing import Literal

logger = logging.getLogger(__name__)


async def refresh_loop():
    while True:
        try:
            await asyncio.to_thread(market_refresh.ensure)
        except HTTPException:
            pass  # State is reported through /health; requests receive 503.
        # Recheck the calendar promptly when a closed market opens.
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app):
    if os.getenv('DEPLOYMENT_MODE', 'local') == 'public_demo' and market_refresh.live:
        raise RuntimeError('Public demo requires DATA_MODE=demo')
    task = asyncio.create_task(refresh_loop()) if market_refresh.live else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="AI Stock Intelligence API", version="0.2.0",
              lifespan=lifespan,
              description="Automatically refreshed stock analytics and rule-based alerts with LLM summaries.")
app.middleware('http')(access_control)
prices = StockPrice.__table__


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    # Avoid exposing connection URLs, query parameters, or driver messages.
    logger.error("Database operation failed: %s on %s", type(exc).__name__, request.url.path)
    return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable"})


def query_records(*, ticker: str | None = None, latest: bool = False,
                  signals: bool = False, limit: int = 50, offset: int = 0,
                  start: date | None = None, end: date | None = None) -> list[dict]:
    market_refresh.ensure()
    observations = StockObservation.__table__
    statement = select(prices, observations.c.observed_at).outerjoin(
        observations, (prices.c.date == observations.c.date) & (prices.c.ticker == observations.c.ticker))
    monitored = list(universe.names(engine))
    statement = statement.where(prices.c.ticker.in_(monitored))
    if ticker:
        statement = statement.where(prices.c.ticker == ticker.upper())
    if latest:
        statement = statement.where(prices.c.date == select(func.max(prices.c.date)).where(prices.c.ticker.in_(monitored)).scalar_subquery())
    if signals:
        statement = statement.where(prices.c.signal.in_(["BIG_GAIN", "BIG_DROP"]))
    if start:
        statement = statement.where(prices.c.date >= datetime.combine(start, datetime.min.time()))
    if end:
        statement = statement.where(prices.c.date <= datetime.combine(end, datetime.max.time()))
    statement = statement.order_by(prices.c.date.desc(), prices.c.ticker).limit(limit).offset(offset)
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    records = [dict(row) for row in rows]
    for record in records:
        observed = record.get('observed_at')
        if observed is not None and observed.tzinfo is None:
            # SQLite test storage drops timezone metadata; observations are UTC.
            record['observed_at'] = observed.replace(tzinfo=timezone.utc)
    return records


def latest_records() -> list[dict]:
    rows = query_records(latest=True, limit=1000)
    if not rows:
        raise HTTPException(status_code=404, detail="No stock data found; run ingestion first")
    return rows


def data_age(value: datetime) -> tuple[int, bool]:
    age = (datetime.now(timezone.utc).date() - value.date()).days
    expected = market_state()['latest_completed_session']
    return age, (value.date().isoformat() < expected if expected else True)


@app.get("/")
def root():
    return {"message": "AI Stock Intelligence API is running"}


@app.get("/health", response_model=HealthResponse)
def health():
    monitored = list(universe.names(engine))
    with engine.connect() as connection:
        row_count, latest = connection.execute(select(func.count(), func.max(prices.c.date)).where(prices.c.ticker.in_(monitored))).one()
        observations = StockObservation.__table__
        count, observed_count, observed_at = connection.execute(
            select(func.count(prices.c.id), func.count(observations.c.observed_at), func.min(observations.c.observed_at))
            .select_from(prices.outerjoin(observations, (prices.c.date == observations.c.date) &
                                         (prices.c.ticker == observations.c.ticker)))
            .where(prices.c.date == latest, prices.c.ticker.in_(monitored))).one()
        snapshot_observed_at = observed_at.isoformat() if observed_at and count == observed_count else None
    age, stale = data_age(latest) if latest else (None, True)
    stale = stale or bool(universe.missing_at(engine, latest))
    refresh = market_refresh.status()
    degraded = stale or (market_refresh.live and (refresh["refresh_error"] is not None or refresh["last_successful_fetch"] is None
                                                  or snapshot_observed_at is None))
    return {"status": "degraded" if degraded else "healthy", "database": "connected",
            "rows": row_count, "latest_date": latest.date().isoformat() if latest else None,
            "age_days": age, "stale": stale, 'latest_snapshot_observed_at': snapshot_observed_at, **refresh}


@app.get('/system/status')
def system_status():
    """Read local state only. Polling this endpoint never refreshes providers."""
    from backend.models import NewsUsage, AlertDelivery
    from backend.news import setting
    now = datetime.now(timezone.utc)
    budgets = {}
    with engine.connect() as connection:
        for name, period, limit in (
            ('daily', now.strftime('day:%Y-%m-%d'), setting('NEWS_DAILY_SEARCH_LIMIT', 20)),
            ('monthly', now.strftime('month:%Y-%m'), setting('NEWS_MONTHLY_SEARCH_LIMIT', 500, 0, 900)),
        ):
            used = connection.scalar(select(NewsUsage.searches).where(NewsUsage.period == period)) or 0
            budgets[name] = {'used': used, 'limit': limit, 'remaining': max(0, limit - used)}
        deliveries = dict(connection.execute(select(AlertDelivery.status, func.count())
                                             .group_by(AlertDelivery.status)).all())
    return {'health': health(), 'market': market_state(), 'ingestion': market_refresh.status(),
            'news_budget_utc': budgets, 'delivery_records': deliveries,
            'delivery_note': 'Database acknowledgments and leases; not inbox delivery confirmation.',
            'process': operational_snapshot()}


@app.get("/stocks", response_model=list[StockRecord])
def get_stocks(ticker: str | None = Query(default=None, min_length=1, max_length=12),
               limit: int = Query(default=50, ge=1, le=1000),
               offset: int = Query(default=0, ge=0), start: date | None = None,
               end: date | None = None):
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    return query_records(ticker=ticker, limit=limit, offset=offset, start=start, end=end)


@app.get("/tickers", response_model=dict[str, list[str]])
def get_tickers():
    market_refresh.ensure()
    with engine.connect() as connection:
        tickers = connection.execute(select(prices.c.ticker).where(prices.c.ticker.in_(universe.names(engine))).distinct().order_by(prices.c.ticker)).scalars().all()
    return {"tickers": tickers}


@app.get("/latest", response_model=list[StockRecord])
def get_latest():
    return latest_records()


@app.get("/signals", response_model=list[StockRecord])
def get_signals(limit: int = Query(default=50, ge=1, le=1000),
                offset: int = Query(default=0, ge=0)):
    return query_records(signals=True, limit=limit, offset=offset)


@app.get("/signals/latest", response_model=list[StockRecord])
def get_latest_signals():
    return [row for row in latest_records() if row["signal"] in {"BIG_GAIN", "BIG_DROP"}]


@app.get("/top-movers", response_model=MoversResponse)
def get_top_movers():
    rows = latest_records()
    ranked = sorted((row for row in rows if row["daily_return_pct"] is not None),
                    key=lambda row: row["daily_return_pct"], reverse=True)
    return {"date": rows[0]["date"].date().isoformat(),
            "top_gainers": [row for row in ranked if row["daily_return_pct"] > 0][:5],
            "top_losers": [row for row in reversed(ranked) if row["daily_return_pct"] < 0][:5]}


@app.get("/ai-summary/latest", response_model=SummaryResponse)
def ai_summary():
    rows = latest_records()
    result = research(engine, [StockRecord.model_validate(row).model_dump(by_alias=True, mode="json") for row in rows],
                      "Summarize the market and explain significant movements", summary=True, summarize=generate_summary)
    return {**with_quantitative(result, rows), **research_metadata(rows)}


def with_quantitative(result, rows, question=None, benchmark='SPY', sessions=21):
    if question and re.search(r'nasdaq|\bqqq\b', question, re.I):
        benchmark = 'QQQ'
    elif question and re.search(r'\bspy\b|s&p|broader market', question, re.I):
        benchmark = 'SPY'
    end = rows[0]['date']
    if question and 'this week' in question.lower():
        from backend.market_calendar import year_schedule
        from datetime import timedelta
        monday = end.date() - timedelta(days=end.weekday())
        calendar = year_schedule(end.year)
        sessions = max(1, sum(monday <= day.date() <= end.date() for day in calendar.index))
    data = quantitative.report(engine, benchmark, sessions, as_of=end)
    answer = quantitative.question_answer(question or 'Compare the monitored stocks with the broader market today', data, universe.names(engine))
    result['quantitative_context'] = data
    if answer and not result.get('answer', '').startswith('Unsupported ticker(s):'):
        if question and result.get('news_status') == 'not_requested':
            result['answer'] = result['summary'] = answer
        else:
            result['answer'] = result.get('answer', '') + ' ' + answer
            result['summary'] = result.get('summary', '') + ' ' + answer
        result['analysis']['observations'].append({'title': f'Benchmark context ({benchmark})', 'text': answer})
    return result


def research_metadata(rows):
    market_date = rows[0]["date"].date().isoformat()
    age, stale = data_age(rows[0]["date"])
    missing = universe.missing_at(engine, rows[0]['date'])
    stale = stale or bool(missing)
    cutoff = analysis_cutoff(market_date)
    observed = [publication_time(str(row.get('observed_at'))) for row in rows]
    if all(observed):
        cutoff = min(cutoff, *observed)
    bounds = session_bounds(market_date)
    unknown_snapshot = market_refresh.live and not all(observed)
    return {"date": market_date, "stale": stale,
            "signals": [row for row in rows if row["signal"] in {"BIG_GAIN", "BIG_DROP"}],
            "last_successful_fetch": market_refresh.last_success,
            "analysis_cutoff": None if unknown_snapshot else cutoff.isoformat(),
            "data_freshness": {"market_date": market_date, "age_days": age, "stale": stale,
                               "missing_monitored_tickers": missing,
                               "last_successful_fetch": market_refresh.last_success,
                               "mode": "live" if market_refresh.live else "demo",
                               "session_status": 'unknown' if unknown_snapshot else 'no_session' if not bounds else 'provisional' if cutoff < bounds[1] else 'closed',
                               "snapshot_observed_at": min(observed).isoformat() if all(observed) else None,
                               "cutoff_convention": "Same New York session, capped at NASDAQ close and persisted fetch-start time when available. Daily bars may be delayed; fetch time is not an exchange quote timestamp."}}


@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest):
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="question must not be blank")
    rows = latest_records()
    result = research(engine, [StockRecord.model_validate(row).model_dump(by_alias=True, mode="json") for row in rows], request.question)
    return {**with_quantitative(result, rows, request.question, request.benchmark, request.sessions), **research_metadata(rows)}


@app.post("/stock-news", response_model=StockNewsResponse)
def stock_news(request: StockNewsRequest):
    """Always research news for exactly the company and session displayed."""
    ticker = request.ticker.strip().upper()
    if ticker not in universe.names(engine):
        raise HTTPException(status_code=422, detail="Select a monitored company")
    rows = query_records(ticker=ticker, start=request.date, end=request.date, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="No stored price for this company and session")
    result = research(engine, [StockRecord.model_validate(row).model_dump(by_alias=True, mode="json") for row in rows],
                      f"What news could help explain {ticker}'s stock move?")
    return {**with_quantitative(result, rows, f'Compare {ticker} with the broader market today'), **research_metadata(rows), "ticker": ticker}


@app.post("/notifications/claim")
def claim_alert(request: AlertClaimRequest):
    rows = latest_records()
    metadata = research_metadata(rows)
    if (request.date != market_state()['market_date']
            or request.date != metadata["date"] or metadata["stale"] or not metadata["signals"]
            or metadata['data_freshness']['session_status'] != 'closed'):
        return {"allowed": False, "reason": "ineligible_snapshot"}
    if "@" not in request.recipient or any(c.isspace() for c in request.recipient):
        raise HTTPException(status_code=422, detail="A recipient address is required")
    claim = claim_delivery(engine, request.date, request.recipient)
    if claim["allowed"]:
        result = research(engine, [StockRecord.model_validate(row).model_dump(by_alias=True, mode="json") for row in rows],
                          "Summarize the market and explain significant movements", summary=True, summarize=generate_summary)
        claim["html"] = render_report({**with_quantitative(result, rows), **metadata})
    return claim


@app.post("/notifications/complete")
def complete_alert(request: AlertCompleteRequest):
    if not complete_delivery(engine, request.key, request.token):
        raise HTTPException(status_code=409, detail="Delivery lease changed or is unavailable")
    return {"recorded": True}


@app.get('/watchlist')
def watchlist():
    return {'stocks': universe.entries(engine), 'benchmarks': universe.BENCHMARKS,
            'max_size': universe.limit(), 'editable': market_refresh.live and os.getenv('DEPLOYMENT_MODE', 'local') == 'local'}


@app.post('/watchlist', status_code=201)
def add_watchlist(request: WatchlistRequest):
    result = universe.change(engine, request.ticker)
    market_refresh.success_clock = None
    return result


@app.delete('/watchlist/{ticker}')
def remove_watchlist(ticker: str):
    return universe.change(engine, ticker, remove=True)


@app.get('/analytics')
def analytics(benchmark: Literal['SPY', 'QQQ'] = 'SPY',
              sessions: int = Query(21, ge=1, le=126), window: int = Query(60, ge=20, le=126)):
    market_refresh.ensure()
    return quantitative.report(engine, benchmark, sessions, window)


@app.get('/benchmarks')
def benchmarks():
    """Benchmark snapshots are separate from monitored equity/alert responses."""
    market_refresh.ensure()
    with engine.connect() as connection:
        rows = []
        for ticker, name in universe.BENCHMARKS.items():
            record = connection.execute(select(prices).where(prices.c.ticker == ticker)
                                        .order_by(prices.c.date.desc()).limit(1)).mappings().first()
            rows.append({'ticker': ticker, 'name': name, 'status': 'ok' if record else 'unavailable',
                         'latest': StockRecord.model_validate(dict(record)).model_dump(by_alias=True, mode='json') if record else None})
    return {'benchmarks': rows}
