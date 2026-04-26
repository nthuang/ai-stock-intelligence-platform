from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint
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