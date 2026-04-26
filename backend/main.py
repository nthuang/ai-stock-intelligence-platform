from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import text

from backend.db import engine
from backend.ai_summary import generate_summary

app = FastAPI(title="AI Stock Intelligence API")


def load_data() -> pd.DataFrame:
    query = """
        SELECT
            date,
            ticker,
            open,
            high,
            low,
            close,
            volume,
            daily_return_pct,
            ma_7,
            ma_30,
            volatility_7,
            signal
        FROM stock_prices
        ORDER BY date, ticker
    """

    df = pd.read_sql(query, con=engine)
    df["date"] = pd.to_datetime(df["date"])
    return df


def format_stock_records(df: pd.DataFrame) -> list[dict]:
    renamed = df.rename(
        columns={
            "date": "Date",
            "ticker": "Ticker",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "daily_return_pct": "Daily Return %",
            "ma_7": "MA_7",
            "ma_30": "MA_30",
            "volatility_7": "Volatility_7",
            "signal": "Signal",
        }
    ).copy()

    return renamed.to_dict(orient="records")


@app.get("/")
def root():
    return {"message": "AI Stock Intelligence API is running"}


@app.get("/stocks")
def get_stocks(ticker: Optional[str] = Query(default=None)):
    df = load_data()

    if ticker:
        df = df[df["ticker"].str.upper() == ticker.upper()]

    if df.empty:
        raise HTTPException(status_code=404, detail="No stock data found")

    return format_stock_records(df.tail(50))


@app.get("/tickers")
def get_tickers():
    df = load_data()
    tickers = sorted(df["ticker"].dropna().unique().tolist())
    return {"tickers": tickers}


@app.get("/top-movers")
def get_top_movers():
    df = load_data()

    latest_date = df["date"].max()
    latest_df = df[df["date"] == latest_date].copy()
    latest_df = latest_df.sort_values("daily_return_pct", ascending=False)

    gainers = latest_df[["ticker", "close", "daily_return_pct", "volume"]].head(5)
    losers = latest_df[["ticker", "close", "daily_return_pct", "volume"]].tail(5)

    return {
        "date": str(latest_date.date()),
        "top_gainers": gainers.rename(
            columns={
                "ticker": "Ticker",
                "close": "Close",
                "daily_return_pct": "Daily Return %",
                "volume": "Volume",
            }
        ).to_dict(orient="records"),
        "top_losers": losers.rename(
            columns={
                "ticker": "Ticker",
                "close": "Close",
                "daily_return_pct": "Daily Return %",
                "volume": "Volume",
            }
        ).to_dict(orient="records"),
    }


@app.get("/latest")
def get_latest():
    df = load_data()
    latest_date = df["date"].max()
    latest_df = df[df["date"] == latest_date].copy()

    return format_stock_records(latest_df)


@app.get("/signals")
def get_signals():
    df = load_data()

    signals_df = df[df["signal"].notna() & (df["signal"] != "NORMAL")].copy()

    if signals_df.empty:
        return {"message": "No abnormal signals found"}

    return signals_df.rename(
        columns={
            "date": "Date",
            "ticker": "Ticker",
            "close": "Close",
            "daily_return_pct": "Daily Return %",
            "volume": "Volume",
            "signal": "Signal",
        }
    )[["Date", "Ticker", "Close", "Daily Return %", "Volume", "Signal"]].to_dict(orient="records")


@app.get("/signals/latest")
def get_latest_signals():
    df = load_data()

    latest_date = df["date"].max()
    latest_df = df[df["date"] == latest_date].copy()

    signals_df = latest_df[
        latest_df["signal"].notna() & (latest_df["signal"] != "NORMAL")
    ]

    if signals_df.empty:
        return {"message": f"No abnormal signals for {latest_date.date()}"}

    return signals_df.rename(
        columns={
            "date": "Date",
            "ticker": "Ticker",
            "close": "Close",
            "daily_return_pct": "Daily Return %",
            "volume": "Volume",
            "signal": "Signal",
        }
    )[["Date", "Ticker", "Close", "Daily Return %", "Volume", "Signal"]].to_dict(orient="records")


@app.get("/ai-summary/latest")
def ai_summary():
    df = load_data()

    latest_date = df["date"].max()
    latest_df = df[df["date"] == latest_date].copy()

    signals = latest_df[
        latest_df["signal"].notna() & (latest_df["signal"] != "NORMAL")
    ]

    signal_list = signals.rename(
        columns={
            "date": "Date",
            "ticker": "Ticker",
            "close": "Close",
            "daily_return_pct": "Daily Return %",
            "volume": "Volume",
            "signal": "Signal",
        }
    ).to_dict(orient="records")

    result = generate_summary(signal_list)

    return {
        "date": str(latest_date.date()),
        "summary": result["summary"],
        "llm_source": result["source"],
        "model": result["model"],
        "signals": signal_list,
    }