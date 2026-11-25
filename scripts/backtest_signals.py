"""Explicit next-session open-to-close experiment; no predictive claims."""
from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_PATH = BASE_DIR / "data" / "processed" / "stocks_processed.csv"
OUTPUT_PATH = BASE_DIR / "data" / "processed" / "backtest_metrics.json"


def calculate_metrics(frame: pd.DataFrame, cost_bps: float = 5.0) -> dict:
    """Allocate 1/N equity per ticker daily, idle sleeves in cash, close nightly.

    Signals observed at day t close execute at day t+1 open and exit at its close.
    Each nonzero sleeve pays cost_bps on entry AND exit. Benchmark buys equal
    dollar sleeves at the first execution day's open and holds to the last close.
    """
    required = {"Date", "Ticker", "Open", "Close", "Signal"}
    if not required.issubset(frame):
        raise ValueError(f"Missing columns: {sorted(required - set(frame.columns))}")
    if not np.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("cost_bps must be finite and nonnegative")
    frame = frame.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="raise")
    if frame[["Date", "Ticker"]].isna().any().any():
        raise ValueError("Missing date or ticker")
    if frame.duplicated(["Date", "Ticker"]).any():
        raise ValueError("Duplicate date/ticker rows")
    frame = frame.sort_values(["Date", "Ticker"])
    dates = frame["Date"].nunique()
    tickers = frame["Ticker"].nunique()
    if dates < 2 or tickers < 1:
        raise ValueError("At least two sessions and one ticker are required")
    if len(frame) != dates * tickers:
        raise ValueError("Backtest requires a complete common-date ticker panel")
    if not np.isfinite(frame[["Open", "Close"]].to_numpy(dtype=float)).all():
        raise ValueError("Prices must be finite")
    if (frame[["Open", "Close"]] <= 0).any().any():
        raise ValueError("Prices must be positive")
    allowed = {"NORMAL", "BIG_GAIN", "BIG_DROP"}
    if not set(frame["Signal"].dropna()).issubset(allowed):
        raise ValueError("Unknown signal")

    position = frame["Signal"].map({"BIG_GAIN": 1.0, "BIG_DROP": -1.0}).fillna(0.0)
    frame["position"] = position.groupby(frame["Ticker"]).shift(1).fillna(0.0)
    execution = frame[frame["Date"] > frame["Date"].min()].copy()
    execution["gross"] = execution["position"] * (execution["Close"] / execution["Open"] - 1)
    execution["net"] = execution["gross"] - execution["position"].abs() * (2 * cost_bps / 10000)
    daily = execution.groupby("Date")["net"].mean()
    if (daily <= -1).any():
        raise ValueError("Portfolio insolvency; limited-capital simulation cannot continue")
    equity = pd.concat([pd.Series([1.0]), (1 + daily).cumprod().reset_index(drop=True)], ignore_index=True)
    drawdown = equity / equity.cummax() - 1
    trades = execution.loc[execution["position"] != 0, "net"]
    grouped = execution.groupby("Ticker")
    benchmark = grouped["Close"].last() / grouped["Open"].first() - 1
    std = daily.std(ddof=1)
    sharpe = float(daily.mean() / std * np.sqrt(252)) if len(daily) > 1 and std > 0 else None
    return {
        "methodology": "prior-close signal; next-open entry; same-day-close exit",
        "start_date": execution["Date"].min().date().isoformat(),
        "end_date": execution["Date"].max().date().isoformat(),
        "tickers": sorted(frame["Ticker"].unique().tolist()),
        "sessions": int(len(daily)),
        "cost_bps_per_side": cost_bps,
        "strategy_total_return_pct": round(float((equity.iloc[-1] - 1) * 100), 4),
        "buy_hold_gross_return_pct": round(float(benchmark.mean() * 100), 4),
        "max_drawdown_pct": round(float(drawdown.min() * 100), 4),
        "annualized_sharpe_zero_risk_free": round(sharpe, 4) if sharpe is not None else None,
        "trade_count": int(len(trades)),
        "win_rate_pct": round(float((trades > 0).mean() * 100), 4) if len(trades) else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=PROCESSED_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--cost-bps", type=float, default=5.0)
    args = parser.parse_args()
    metrics = calculate_metrics(pd.read_csv(args.input), cost_bps=args.cost_bps)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
