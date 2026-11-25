from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class StockPrice(Base):
    __tablename__ = "stock_prices"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False, index=True)
    ticker = Column(String, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    daily_return_pct = Column(Float, nullable=True)
    ma_7 = Column(Float, nullable=True)
    ma_30 = Column(Float, nullable=True)
    volatility_7 = Column(Float, nullable=True)
    signal = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("date", "ticker", name="uq_stock_date_ticker"),
    )


class NewsCache(Base):
    __tablename__ = "news_cache"
    key = Column(String(64), primary_key=True)
    payload = Column(JSON, nullable=False)
    expires_at = Column(Float, nullable=False)


class NewsUsage(Base):
    __tablename__ = "news_usage"
    period = Column(String(16), primary_key=True)
    searches = Column(Integer, nullable=False, default=0)


class ResearchCache(Base):
    __tablename__ = "research_cache"
    key = Column(String(64), primary_key=True)
    payload = Column(JSON, nullable=False)
    expires_at = Column(Float, nullable=False)


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    key = Column(String(64), primary_key=True)
    token = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    expires_at = Column(Float, nullable=False)
