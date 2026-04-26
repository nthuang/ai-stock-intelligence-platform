from pathlib import Path
from datetime import datetime
import os

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


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
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

engine = create_engine(DATABASE_URL)


def ensure_directories() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def fetch_ticker_data(ticker: str, period: str = PERIOD, interval: str = INTERVAL) -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False)

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
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["Ticker", "Date"]).copy()

    df["Daily Return %"] = df.groupby("Ticker")["Close"].pct_change() * 100
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

    return db_df[ordered_columns]


def upsert_to_postgres(df: pd.DataFrame) -> None:
    db_df = prepare_db_dataframe(df)

    temp_table = "stock_prices_staging"

    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {temp_table};"))
        db_df.to_sql(temp_table, con=connection, if_exists="replace", index=False)

        connection.execute(
            text(
                f"""
                INSERT INTO stock_prices (
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
                )
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
                FROM {temp_table}
                ON CONFLICT (date, ticker)
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    daily_return_pct = EXCLUDED.daily_return_pct,
                    ma_7 = EXCLUDED.ma_7,
                    ma_30 = EXCLUDED.ma_30,
                    volatility_7 = EXCLUDED.volatility_7,
                    signal = EXCLUDED.signal;
                """
            )
        )

        connection.execute(text(f"DROP TABLE IF EXISTS {temp_table};"))


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

    if not all_frames:
        raise RuntimeError("No stock data was fetched successfully.")

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