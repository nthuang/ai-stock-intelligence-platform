from pathlib import Path
from datetime import datetime
import os

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert
from backend.models import StockPrice


TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
PERIOD = "6mo"
INTERVAL = "1d"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

env_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL) if DATABASE_URL else None


def ensure_directories() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def fetch_ticker_data(ticker: str, period: str = PERIOD, interval: str = INTERVAL) -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, auto_adjust=False,
                     progress=False, threads=False, timeout=10)

    if df.empty:
        raise ValueError(f"No data returned for ticker: {ticker}")

    df = df.reset_index()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    required_cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns for {ticker}: {missing}")

    df = df[required_cols].copy()
    df["Ticker"] = ticker
    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume"])
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["Ticker", "Date"]).copy()

    df["Daily Return %"] = (
        df.groupby("Ticker", sort=False)["Close"]
        .pct_change(fill_method=None)
        .mul(100)
    )
    df["MA_7"] = df.groupby("Ticker")["Close"].transform(lambda s: s.rolling(7).mean())
    df["MA_30"] = df.groupby("Ticker")["Close"].transform(lambda s: s.rolling(30).mean())
    df["Volatility_7"] = df.groupby("Ticker")["Daily Return %"].transform(lambda s: s.rolling(7).std())

    df["Signal"] = "NORMAL"
    df.loc[df["Daily Return %"] > 3, "Signal"] = "BIG_GAIN"
    df.loc[df["Daily Return %"] < -3, "Signal"] = "BIG_DROP"
    df.loc[df["Daily Return %"].isna(), "Signal"] = None

    return df


def save_raw_data(df: pd.DataFrame) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = RAW_DIR / f"stocks_raw_{today}.csv"
    df.to_csv(output_path, index=False)
    return output_path


def save_processed_data(df: pd.DataFrame) -> Path:
    output_path = PROCESSED_DIR / "stocks_processed.csv"
    df.to_csv(output_path, index=False)
    return output_path


def prepare_db_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    db_df = df.rename(
        columns={
            "Date": "date",
            "Ticker": "ticker",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "Daily Return %": "daily_return_pct",
            "MA_7": "ma_7",
            "MA_30": "ma_30",
            "Volatility_7": "volatility_7",
            "Signal": "signal",
        }
    ).copy()

    ordered_columns = [
        "date",
        "ticker",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "daily_return_pct",
        "ma_7",
        "ma_30",
        "volatility_7",
        "signal",
    ]

    db_df = db_df[ordered_columns].replace([float("inf"), float("-inf")], pd.NA)
    db_df = db_df.dropna(subset=["date", "ticker", "open", "high", "low", "close", "volume"])
    if db_df.empty:
        raise ValueError("No complete stock rows available for PostgreSQL")
    if db_df.duplicated(["date", "ticker"]).any():
        raise ValueError("Duplicate date/ticker rows in ingestion batch")
    if (db_df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Prices must be positive")
    if (db_df["volume"] < 0).any():
        raise ValueError("Volume must be nonnegative")
    return db_df


def upsert_to_postgres(df: pd.DataFrame) -> None:
    if engine is None:
        raise RuntimeError("DATABASE_URL is not set in .env")

    db_df = prepare_db_dataframe(df)

    # Native upsert avoids a shared staging table and is safe for overlapping jobs.
    records = db_df.astype(object).where(pd.notna(db_df), None).to_dict("records")
    with engine.begin() as connection:
        for offset in range(0, len(records), 1000):
            statement = insert(StockPrice.__table__).values(records[offset:offset + 1000])
            statement = statement.on_conflict_do_update(
                index_elements=["date", "ticker"],
                set_={column: getattr(statement.excluded, column)
                      for column in db_df.columns if column not in {"date", "ticker"}},
            )
            connection.execute(statement)


def main() -> None:
    ensure_directories()

    all_frames = []

    for ticker in TICKERS:
        try:
            print(f"Fetching {ticker}...")
            ticker_df = fetch_ticker_data(ticker)
            all_frames.append(ticker_df)
        except Exception as exc:
            print(f"Failed to fetch {ticker}: {exc}")

    if len(all_frames) != len(TICKERS):
        raise RuntimeError("Incomplete ticker batch; database and snapshots were not updated")

    raw_df = pd.concat(all_frames, ignore_index=True)
    processed_df = add_indicators(raw_df)

    raw_path = save_raw_data(raw_df)
    processed_path = save_processed_data(processed_df)

    upsert_to_postgres(processed_df)

    print("\nDone.")
    print(f"Raw data saved to: {raw_path}")
    print(f"Processed data saved to: {processed_path}")
    print("Data upserted to PostgreSQL table: stock_prices")
    print("\nPreview:")
    print(processed_df.tail(10))


if __name__ == "__main__":
    main()
