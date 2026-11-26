"""Public response contracts; aliases retain dashboard-friendly column names."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StockRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date: datetime = Field(alias="Date")
    ticker: str = Field(alias="Ticker")
    open: float = Field(alias="Open")
    high: float = Field(alias="High")
    low: float = Field(alias="Low")
    close: float = Field(alias="Close")
    volume: float = Field(alias="Volume")
    daily_return_pct: float | None = Field(default=None, alias="Daily Return %")
    ma_7: float | None = Field(default=None, alias="MA_7")
    ma_30: float | None = Field(default=None, alias="MA_30")
    volatility_7: float | None = Field(default=None, alias="Volatility_7")
    signal: str | None = Field(default=None, alias="Signal")
    observed_at: datetime | None = Field(default=None, alias="Observed At")


class SummaryResponse(BaseModel):
    quantitative_context: dict = Field(default_factory=dict)
    date: str
    summary: str
    llm_source: Literal["rule", "groq", "fallback"]
    model: str
    stale: bool
    signals: list[StockRecord]
    provider_error: str | None = None
    last_successful_fetch: str | None = None
    performance: list[StockRecord] = Field(default_factory=list)
    market_overview: str = ""
    analysis: dict = Field(default_factory=dict)
    analyst_commentary: str = ""
    news_message: str = ""
    premise_note: str = ""
    sources: list[dict] = Field(default_factory=list)
    explanations: list[dict] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    news_status: str = "not_requested"
    news_retrieval: dict = Field(default_factory=dict)
    generated_at: str | None = None
    analysis_cutoff: str | None = None
    cache_hit: bool = False
    data_freshness: dict = Field(default_factory=dict)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    benchmark: Literal['SPY', 'QQQ'] = 'SPY'
    sessions: int = Field(default=21, ge=1, le=126)


class WatchlistRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=8)


class AskResponse(SummaryResponse):
    answer: str


class StockNewsRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    date: date


class StockNewsResponse(AskResponse):
    ticker: str


class AlertClaimRequest(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    recipient: str = Field(min_length=3, max_length=320)


class AlertCompleteRequest(BaseModel):
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    token: str = Field(pattern=r"^[a-f0-9]{48}$")


class HealthResponse(BaseModel):
    latest_snapshot_observed_at: str | None = None
    status: Literal["healthy", "degraded"]
    database: str
    rows: int
    latest_date: str | None
    age_days: int | None
    stale: bool
    mode: str
    refresh_interval_seconds: int
    last_successful_fetch: str | None
    last_refresh_attempt: str | None
    refresh_error: str | None


class MoversResponse(BaseModel):
    date: str
    top_gainers: list[StockRecord]
    top_losers: list[StockRecord]
