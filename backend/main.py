"""Read-only analytics API with bounded SQL queries and explicit contracts."""
from datetime import date, datetime, timezone
import logging
import os
from pathlib import Path
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.ai_summary import generate_summary
from backend.db import engine
from backend.models import StockPrice
from backend.refresh import market_refresh
from backend.schemas import HealthResponse, MoversResponse, StockRecord, SummaryResponse

logger = logging.getLogger(__name__)


async def refresh_loop():
    while True:
        try:
            await asyncio.to_thread(market_refresh.ensure)
        except HTTPException:
            pass  # State is reported through /health; requests receive 503.
        await asyncio.sleep(market_refresh.interval)


@asynccontextmanager
async def lifespan(app):
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
    statement = select(prices)
    if ticker:
        statement = statement.where(prices.c.ticker == ticker.upper())
    if latest:
        statement = statement.where(prices.c.date == select(func.max(prices.c.date)).scalar_subquery())
    if signals:
        statement = statement.where(prices.c.signal.in_(["BIG_GAIN", "BIG_DROP"]))
    if start:
        statement = statement.where(prices.c.date >= datetime.combine(start, datetime.min.time()))
    if end:
        statement = statement.where(prices.c.date <= datetime.combine(end, datetime.max.time()))
    statement = statement.order_by(prices.c.date.desc(), prices.c.ticker).limit(limit).offset(offset)
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [dict(row) for row in rows]


def latest_records() -> list[dict]:
    rows = query_records(latest=True, limit=1000)
    if not rows:
        raise HTTPException(status_code=404, detail="No stock data found; run ingestion first")
    return rows


def data_age(value: datetime) -> tuple[int, bool]:
    age = (datetime.now(timezone.utc).date() - value.date()).days
    return age, age > int(os.getenv("MAX_DATA_AGE_DAYS", "4"))


@app.get("/")
def root():
    return {"message": "AI Stock Intelligence API is running"}


@app.get("/health", response_model=HealthResponse)
def health():
    with engine.connect() as connection:
        row_count, latest = connection.execute(select(func.count(), func.max(prices.c.date))).one()
    age, stale = data_age(latest) if latest else (None, True)
    refresh = market_refresh.status()
    degraded = stale or (market_refresh.live and (refresh["refresh_error"] is not None or refresh["last_successful_fetch"] is None))
    return {"status": "degraded" if degraded else "healthy", "database": "connected",
            "rows": row_count, "latest_date": latest.date().isoformat() if latest else None,
            "age_days": age, "stale": stale, **refresh}


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
        tickers = connection.execute(select(prices.c.ticker).distinct().order_by(prices.c.ticker)).scalars().all()
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
    signals = [row for row in rows if row["signal"] in {"BIG_GAIN", "BIG_DROP"}]
    result = generate_summary([StockRecord.model_validate(row).model_dump(by_alias=True) for row in rows])
    return {"date": rows[0]["date"].date().isoformat(), "summary": result["summary"],
            "llm_source": result["source"], "model": result["model"],
            "stale": data_age(rows[0]["date"])[1], "signals": signals,
            "provider_error": result.get("provider_error"),
            "last_successful_fetch": market_refresh.last_success}
