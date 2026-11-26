"""Explicit one-off Yahoo download aligned to the immutable stock demo fixture."""
import argparse
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/processed/benchmarks_demo.csv')
    output = parser.parse_args().output
    if output.exists():
        raise ValueError('Refusing to overwrite an existing demo fixture; choose a new output path')
    stocks = pd.read_csv(ROOT / 'data/processed/stocks_processed.csv')
    dates = pd.DatetimeIndex(pd.to_datetime(stocks.Date.unique())).normalize().sort_values()
    frames = []
    for ticker in ('SPY', 'QQQ'):
        frame = yf.download(ticker, start=dates.min().date().isoformat(),
                            end=(dates.max() + pd.Timedelta(days=1)).date().isoformat(),
                            auto_adjust=False, progress=False, threads=False, timeout=10)
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        frame = frame.reset_index()[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
        frame.Date = pd.to_datetime(frame.Date).dt.tz_localize(None).dt.normalize()
        frame = frame[frame.Date.isin(dates)].copy()
        if set(frame.Date) != set(dates) or frame.isna().any().any():
            raise ValueError('Incomplete benchmark history; nothing exported')
        frame['Ticker'] = ticker
        frames.append(frame)
    pd.concat(frames, ignore_index=True).to_csv(output, index=False)
    print(f'Exported {sum(len(frame) for frame in frames)} benchmark rows, {dates.min().date()} through {dates.max().date()}')


if __name__ == '__main__':
    main()
