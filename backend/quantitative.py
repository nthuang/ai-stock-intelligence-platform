"""Session-aligned price-return analytics. All numbers are computed, never generated."""
from datetime import timedelta
import math
import re

import numpy as np
import pandas as pd
from sqlalchemy import select, func

from backend.models import StockPrice
from backend.market_calendar import year_schedule
from backend.universe import names, BENCHMARKS


def finite(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def metrics(frame, tickers, benchmark, *, sessions=21, window=60, as_of=None):
    """Require identical calendar endpoints; never forward-fill missing observations."""
    if frame.empty:
        return {'status': 'unavailable', 'benchmark': benchmark, 'stocks': [], 'correlations': {}, 'series': []}
    frame = frame.copy()
    frame['date'] = pd.to_datetime(frame['date']).dt.normalize()
    end = pd.Timestamp(as_of or frame['date'].max()).normalize()
    schedules = pd.concat([year_schedule(y) for y in range((end - timedelta(days=400)).year, end.year + 1)])
    dates = schedules.index[schedules.index <= end][-max(sessions + 1, window + 2, 23):]
    if not len(dates):
        return {'status': 'unavailable', 'benchmark': benchmark, 'stocks': [], 'correlations': {}, 'series': []}
    close = frame.pivot(index='date', columns='ticker', values='close').reindex(index=dates, columns=[*tickers, benchmark])
    close = close.where(np.isfinite(close) & (close > 0))
    volume = frame.pivot(index='date', columns='ticker', values='volume').reindex(index=dates, columns=tickers)
    volume = volume.where(np.isfinite(volume) & (volume >= 0))
    returns = close.pct_change(fill_method=None)
    period = close.tail(sessions + 1)
    risk = returns.tail(window)

    def cumulative(ticker):
        values = period[ticker]
        return finite((values.iloc[-1] / values.iloc[0] - 1) * 100) if len(values) == sessions + 1 and values.notna().all() else None

    benchmark_total = cumulative(benchmark)
    benchmark_daily = finite(returns[benchmark].iloc[-1] * 100)
    stocks = []
    for ticker in tickers:
        observed = risk[ticker].dropna()
        complete = len(observed) == window
        std = observed.std(ddof=1) if complete else None
        total = cumulative(ticker)
        daily = finite(returns[ticker].iloc[-1] * 100)
        pair = risk[[ticker, benchmark]].dropna()
        beta = None
        if len(pair) == window and pair[benchmark].var(ddof=1) > 1e-16:
            beta = finite(pair[ticker].cov(pair[benchmark]) / pair[benchmark].var(ddof=1))
        previous_volume = volume[ticker].iloc[:-1].tail(20)
        relative_volume = None
        if previous_volume.count() == 20 and previous_volume.mean() > 0:
            relative_volume = finite(volume[ticker].iloc[-1] / previous_volume.mean())
        baseline = returns[ticker].iloc[:-1].tail(20)
        zscore = None
        if baseline.count() == 20 and baseline.std(ddof=1) > 1e-16:
            zscore = finite((returns[ticker].iloc[-1] - baseline.mean()) / baseline.std(ddof=1))
        drawdown = finite((period[ticker] / period[ticker].cummax() - 1).min() * 100) if total is not None else None
        stocks.append({'ticker': ticker, 'return_observations': len(observed),
                       'status': 'ok' if total is not None else 'insufficient_period_history',
                       'daily_return_pct': daily, 'cumulative_return_pct': total,
                       'relative_daily_pp': finite(daily - benchmark_daily) if daily is not None and benchmark_daily is not None else None,
                       'relative_return_pp': finite(total - benchmark_total) if total is not None and benchmark_total is not None else None,
                       'annualized_volatility_pct': finite(std * np.sqrt(252) * 100) if complete else None,
                       'sharpe_zero_rf': finite(observed.mean() / std * np.sqrt(252)) if complete and std > 1e-8 else None,
                       'beta': beta, 'max_drawdown_pct': drawdown, 'relative_volume': relative_volume,
                       'volume_anomaly': relative_volume >= 2 if relative_volume is not None else None,
                       'return_zscore': zscore})
    correlations = {}
    for first in tickers:
        correlations[first] = {}
        for second in tickers:
            pair = risk[[first, second]] if first != second else risk[[first]]
            complete = len(pair.dropna()) == window and all(pair.std(ddof=1) > 1e-8)
            correlations[first][second] = finite(risk[first].corr(risk[second])) if complete else None
    series = []
    for day, values in period.iterrows():
        series.append({'date': day.date().isoformat(), 'returns_pct': {
            ticker: finite((values[ticker] / period[ticker].iloc[0] - 1) * 100)
            for ticker in [*tickers, benchmark]}})
    return {'status': 'ok', 'as_of': dates[-1].date().isoformat(),
            'start': period.index[0].date().isoformat(), 'sessions': sessions, 'window': window,
            'benchmark': benchmark, 'benchmark_status': 'ok' if benchmark_total is not None else 'insufficient_history',
            'benchmark_return_pct': benchmark_total, 'benchmark_daily_return_pct': benchmark_daily,
            'stocks': stocks, 'correlations': correlations, 'series': series,
            'methodology': 'Unadjusted Close price returns, not total returns. 252 sessions/year; sample standard deviation; Sharpe risk-free rate 0. No gap filling. Beta/correlation require the full common rolling window. Volume and Z-score baselines exclude today.'}


def report(engine, benchmark='SPY', sessions=21, window=60, as_of=None):
    tickers = list(names(engine))
    table = StockPrice.__table__
    with engine.connect() as connection:
        latest = as_of or connection.scalar(select(func.max(table.c.date)).where(table.c.ticker.in_(tickers)))
        if latest is None:
            return metrics(pd.DataFrame(), tickers, benchmark)
        latest = pd.Timestamp(latest).to_pydatetime()
        rows = connection.execute(select(table.c.date, table.c.ticker, table.c.close, table.c.volume).where(
            table.c.ticker.in_([*tickers, benchmark]), table.c.date <= latest,
            table.c.date >= latest - timedelta(days=400))).mappings().all()
    return metrics(pd.DataFrame(rows), tickers, benchmark, sessions=sessions, window=window, as_of=latest)


def question_answer(question, data, companies):
    """Return deterministic answers for supported analytical intents; no provider calls."""
    q = question.lower()
    if not re.search(r'benchmark|broader market|nasdaq|s&p|spy|qqq|outperform|underperform|beta|correlat|volatil|sharpe|drawdown|z.score|volume|cumulative|compare', q):
        return None
    selected = [row for row in data['stocks'] if
                re.search(r'\b' + re.escape(row['ticker']) + r'\b', question, re.I if len(row['ticker']) > 2 else 0)
                or any(value and re.search(r'\b' + re.escape(value) + r'\b', question, re.I)
                       for value in (companies.get(row['ticker']), companies.get(row['ticker'], '').split(' ')[0]))]
    rows = selected or data['stocks']
    if 'correlat' in q:
        symbols = [row['ticker'] for row in rows]
        if len(symbols) != 2:
            return 'Specify two monitored stocks to compare their rolling return correlation.'
        value = data['correlations'][symbols[0]][symbols[1]]
        return f"{symbols[0]} / {symbols[1]} {data['window']}-session return correlation: " + (f'{value:.3f}.' if value is not None else 'unavailable; insufficient common history or zero variance.')
    field = next((field for word, field in [('beta', 'beta'), ('volatil', 'annualized_volatility_pct'),
        ('sharpe', 'sharpe_zero_rf'), ('drawdown', 'max_drawdown_pct'), ('volume', 'relative_volume'),
        ('z-score', 'return_zscore')] if word in q), None)
    if field:
        eligible = [row for row in rows if row[field] is not None]
        if 'highest' in q or 'most' in q:
            rows = [max(eligible, key=lambda row: row[field])] if eligible else []
        if not rows:
            return 'This statistic is unavailable: insufficient comparable history or zero variance.'
        label = {'annualized_volatility_pct': 'annualized volatility (%)', 'sharpe_zero_rf': 'Sharpe (zero risk-free rate)',
                 'max_drawdown_pct': 'maximum drawdown (%)', 'relative_volume': 'relative volume (times)',
                 'return_zscore': 'return Z-score', 'beta': 'beta'}[field]
        note = (' Baseline: preceding 20 sessions, excluding today.' if field in {'relative_volume', 'return_zscore'} else
                f" Period: {data['sessions']} trading sessions." if field == 'max_drawdown_pct' else
                f" Rolling window: {data['window']} sessions; benchmark {data['benchmark']}; Sharpe assumes zero risk-free rate.")
        return ' '.join(f"{row['ticker']} {label}: {row[field]:.3f}." if row[field] is not None else f"{row['ticker']} {label}: unavailable." for row in rows) + note
    daily = bool(re.search(r'today|daily|selloff', q))
    field = 'relative_daily_pp' if daily else 'relative_return_pp'
    if 'which' in q and 'outperform' in q:
        eligible = [row for row in rows if row[field] is not None]
        if not eligible:
            return 'Benchmark comparison unavailable: insufficient aligned price history.'
        rows = [row for row in eligible if row[field] > 0]
        if not rows:
            return 'No monitored stock outperformed the selected benchmark over this period.'
    benchmark_return = data['benchmark_daily_return_pct'] if daily else data['benchmark_return_pct']
    observed_field = 'daily_return_pct' if daily else 'cumulative_return_pct'
    period = 'daily' if daily else str(data['sessions']) + '-session'
    return ' '.join(f"{row['ticker']} {period} return {row[observed_field]:+.2f}% versus {data['benchmark']} {benchmark_return:+.2f}%; difference {row[field]:+.2f} percentage points." if row[field] is not None else f"{row['ticker']} comparison with {data['benchmark']}: unavailable (insufficient aligned history)." for row in rows) + ' Benchmark association does not establish causation.'
