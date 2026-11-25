import pytest
from backend.analysis import analyze_snapshot, stock_analysis


def test_analysis_calculates_breadth_dispersion_and_trend_gaps():
    rows = [dict(Ticker='AAPL', Close=110, Open=112, MA_7=100, MA_30=120, Volatility_7=2,
                 **{'Daily Return %': 4, 'Signal': 'BIG_GAIN'}),
            dict(Ticker='MSFT', Close=90, Open=88, MA_7=95, MA_30=80, Volatility_7=1,
                 **{'Daily Return %': -2, 'Signal': 'NORMAL'})]
    result = analyze_snapshot(rows)
    assert result['metrics']['equal_weight_return_pct'] == 1
    assert result['metrics']['median_return_pct'] == 1
    assert result['metrics']['spread_percentage_points'] == 6
    assert result['metrics']['above_ma_30'] == 1
    apple, microsoft = result['stocks']
    assert apple['gap_ma_7_pct'] == pytest.approx(10)
    assert apple['gap_ma_30_pct'] == pytest.approx(-8.3333333)
    assert apple['intraday_return_pct'] == pytest.approx(-1.78571428)
    assert apple['move_to_volatility_ratio'] == 2
    assert 'rebound' in apple['interpretation']
    assert 'opposed the daily return' in apple['interpretation']
    assert 'pullback' in microsoft['interpretation']
    assert 'masks losses' in result['observations'][1]['text']


def test_missing_history_is_not_treated_as_zero_or_a_trend():
    rows = [{'Ticker': 'AAPL', 'Daily Return %': None, 'MA_30': None},
            {'Ticker': 'MSFT', 'Daily Return %': 2}]
    result = analyze_snapshot(rows)
    assert result['metrics']['equal_weight_return_pct'] == 2
    assert result['metrics']['missing_returns'] == 1
    assert result['metrics']['ma_30_count'] == 0
    assert result['stocks'][0]['gap_ma_30_pct'] is None
    assert 'insufficient' in result['stocks'][0]['interpretation']
    assert analyze_snapshot([])['metrics'] == {}


def test_zero_volatility_does_not_claim_an_infinite_outlier():
    stock = stock_analysis({'Ticker': 'AAPL', 'Daily Return %': 4, 'Volatility_7': 0})
    assert stock['move_to_volatility_ratio'] is None
