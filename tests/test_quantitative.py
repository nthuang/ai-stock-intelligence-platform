import math
import numpy as np
import pandas as pd
import pytest

from backend.quantitative import metrics, question_answer
from backend.market_calendar import year_schedule


def panel(count=90):
    dates = year_schedule(2026).index[:count + 1]
    returns = np.resize([.012, -.02, .018, .005, -.01], count)
    frames = []
    for ticker, multiple in [('TEST', 2), ('OTHER', .5), ('SPY', 1)]:
        prices = np.r_[100, 100 * np.cumprod(1 + returns * multiple)]
        frames.append(pd.DataFrame({'date': dates, 'ticker': ticker, 'close': prices, 'volume': [100] * count + [300]}))
    return pd.concat(frames, ignore_index=True), returns


def test_reference_beta_correlation_volatility_sharpe_and_volume():
    frame, returns = panel()
    result = metrics(frame, ['TEST', 'OTHER'], 'SPY')
    stock = result['stocks'][0]
    expected = returns[-60:] * 2
    assert stock['beta'] == pytest.approx(2)
    assert result['correlations']['TEST']['OTHER'] == pytest.approx(1)
    assert stock['annualized_volatility_pct'] == pytest.approx(np.std(expected, ddof=1) * math.sqrt(252) * 100)
    assert stock['sharpe_zero_rf'] == pytest.approx(np.mean(expected) / np.std(expected, ddof=1) * math.sqrt(252))
    assert stock['relative_volume'] == 3
    assert stock['volume_anomaly'] is True
    assert stock['return_zscore'] == pytest.approx((returns[-1] - returns[-21:-1].mean()) / returns[-21:-1].std(ddof=1))


def test_known_cumulative_relative_and_drawdown_values():
    dates = year_schedule(2026).index[:4]
    frame = pd.DataFrame({'date': list(dates) * 2, 'ticker': ['TEST'] * 4 + ['SPY'] * 4,
                          'close': [100, 120, 90, 108, 100, 110, 110, 110], 'volume': [100] * 8})
    result = metrics(frame, ['TEST'], 'SPY', sessions=3)
    stock = result['stocks'][0]
    assert stock['cumulative_return_pct'] == pytest.approx(8)
    assert stock['relative_return_pp'] == pytest.approx(-2)
    assert stock['relative_daily_pp'] == pytest.approx(20)
    assert stock['max_drawdown_pct'] == pytest.approx(-25)
    assert stock['beta'] is None and stock['sharpe_zero_rf'] is None


def test_missing_session_is_not_forward_filled_or_treated_as_daily():
    frame, _ = panel()
    missing = frame[(frame.ticker == 'SPY')].index[-2]
    result = metrics(frame.drop(missing), ['TEST', 'OTHER'], 'SPY')
    assert result['benchmark_status'] == 'insufficient_history'
    assert result['stocks'][0]['relative_daily_pp'] is None
    assert result['stocks'][0]['relative_return_pp'] is None
    assert result['stocks'][0]['beta'] is None


def test_zero_variance_zero_volume_and_short_history_return_null():
    frame, _ = panel()
    frame['close'] = 100
    frame['volume'] = 0
    stock = metrics(frame, ['TEST', 'OTHER'], 'SPY')['stocks'][0]
    assert stock['annualized_volatility_pct'] == 0
    assert stock['beta'] is None and stock['sharpe_zero_rf'] is None
    assert stock['relative_volume'] is None and stock['return_zscore'] is None
    assert metrics(frame, ['TEST', 'OTHER'], 'SPY')['correlations']['TEST']['OTHER'] is None
    short = metrics(frame.tail(2), ['TEST', 'OTHER'], 'SPY')
    assert short['stocks'][0]['cumulative_return_pct'] is None


def test_no_future_observations_in_historical_analytics():
    frame, _ = panel()
    cutoff = frame.date.sort_values().unique()[-10]
    old = metrics(frame, ['TEST', 'OTHER'], 'SPY', as_of=cutoff)
    frame.loc[frame.date > cutoff, 'close'] = 99999
    assert metrics(frame, ['TEST', 'OTHER'], 'SPY', as_of=cutoff) == old


def test_quantitative_questions_use_computed_values_without_generation():
    frame, _ = panel()
    data = metrics(frame, ['TEST', 'OTHER'], 'SPY')
    companies = {'TEST': 'Test Company', 'OTHER': 'Other Company'}
    assert 'TEST beta: 2.000' in question_answer('Which stock has the highest beta?', data, companies)
    assert '1.000' in question_answer('How correlated are TEST and OTHER?', data, companies)
    assert 'percentage points' in question_answer('Compare TEST with SPY today', data, companies)
