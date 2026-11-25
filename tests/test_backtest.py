import pandas as pd
import pytest

from scripts.backtest_signals import calculate_metrics


def panel():
    return pd.DataFrame({
        "Ticker": ["AAPL"] * 3,
        "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "Open": [100.0, 100.0, 110.0],
        "Close": [104.0, 110.0, 99.0],
        "Signal": ["BIG_GAIN", "BIG_DROP", "NORMAL"],
    })


def test_next_open_execution_compounding_and_costs():
    result = calculate_metrics(panel(), cost_bps=5)
    # Two profitable 10% trades, each less 0.1% round-trip costs.
    assert result["strategy_total_return_pct"] == round((1.099 ** 2 - 1) * 100, 4)
    assert result["buy_hold_gross_return_pct"] == -1.0
    assert result["trade_count"] == 2
    assert result["win_rate_pct"] == 100.0


def test_no_signals_stays_in_cash_benchmark_still_moves():
    frame = panel()
    frame["Signal"] = "NORMAL"
    result = calculate_metrics(frame)
    assert result["strategy_total_return_pct"] == 0
    assert result["buy_hold_gross_return_pct"] == -1
    assert result["trade_count"] == 0
    assert result["annualized_sharpe_zero_risk_free"] is None


def test_allocation_includes_idle_ticker():
    first = panel()
    second = panel().assign(Ticker="MSFT", Signal="NORMAL")
    result = calculate_metrics(pd.concat([first, second]), cost_bps=0)
    assert result["strategy_total_return_pct"] == 10.25


def test_last_signal_cannot_trade_in_same_session():
    frame = panel()
    frame["Signal"] = ["NORMAL", "NORMAL", "BIG_GAIN"]
    assert calculate_metrics(frame)["trade_count"] == 0


def test_drawdown_includes_initial_capital():
    frame = panel().iloc[:2].copy()
    frame.loc[1, "Close"] = 90
    assert calculate_metrics(frame, cost_bps=0)["max_drawdown_pct"] == -10


def test_rejects_duplicate_and_incomplete_panels():
    frame = panel()
    with pytest.raises(ValueError, match="Duplicate"):
        calculate_metrics(pd.concat([frame, frame.iloc[:1]]))
    with pytest.raises(ValueError, match="common-date"):
        calculate_metrics(pd.concat([frame, frame.iloc[:2].assign(Ticker="MSFT")]))
    with pytest.raises(ValueError, match="nonnegative"):
        calculate_metrics(frame, cost_bps=-1)
