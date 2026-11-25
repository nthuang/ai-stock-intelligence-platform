import pandas as pd

from scripts.fetch_data import add_indicators, prepare_db_dataframe


def test_add_indicators_preserves_tickers_and_sets_signals():
    frame = pd.DataFrame(
        {
            "Ticker": ["AAPL"] * 3,
            "Date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "Close": [100.0, 104.0, 100.0],
            "Open": [99.0, 103.0, 101.0],
            "High": [101.0, 105.0, 102.0],
            "Low": [98.0, 102.0, 99.0],
            "Volume": [1000, 1100, 900],
        }
    )

    result = add_indicators(frame)

    assert "MA_7" in result
    assert "Volatility_7" in result
    assert result.iloc[1]["Signal"] == "BIG_GAIN"
    assert result.iloc[2]["Signal"] == "BIG_DROP"


def test_prepare_db_dataframe_uses_database_column_names():
    frame = pd.DataFrame(
        {
            "Ticker": ["AAPL"],
            "Date": [pd.Timestamp("2026-01-01")],
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1000],
            "Daily Return %": [0.5],
            "MA_7": [100.0],
            "MA_30": [99.0],
            "Volatility_7": [0.2],
            "Signal": ["NORMAL"],
        }
    )

    result = prepare_db_dataframe(frame)

    assert list(result.columns) == [
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


def test_prepare_db_dataframe_rejects_incomplete_rows():
    frame = pd.DataFrame(
        {
            "Ticker": ["AAPL", "MSFT"],
            "Date": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")],
            "Open": [100.0, 200.0],
            "High": [101.0, 201.0],
            "Low": [99.0, 199.0],
            "Close": [100.5, None],
            "Volume": [1000, 2000],
            "Daily Return %": [0.5, None],
            "MA_7": [100.0, None],
            "MA_30": [99.0, None],
            "Volatility_7": [0.2, None],
            "Signal": ["NORMAL", None],
        }
    )

    result = prepare_db_dataframe(frame)

    assert len(result) == 1
    assert result.iloc[0]["ticker"] == "AAPL"



def test_indicators_do_not_cross_ticker_boundaries():
    frame = pd.DataFrame({'Ticker': ['AAPL', 'MSFT', 'AAPL', 'MSFT'],
                          'Date': pd.to_datetime(['2026-01-01', '2026-01-01', '2026-01-02', '2026-01-02']),
                          'Close': [100.0, 200.0, 104.0, 190.0]})
    result = add_indicators(frame)
    assert result[result.Ticker == 'MSFT'].iloc[0]['Signal'] is None
    assert result[result.Ticker == 'MSFT'].iloc[1]['Signal'] == 'BIG_DROP'


def test_partial_fetch_does_not_update_database_or_snapshots(monkeypatch):
    import pytest
    from scripts import fetch_data

    monkeypatch.setattr(fetch_data, 'ensure_directories', lambda: None)
    def fail(ticker):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(fetch_data, 'fetch_ticker_data', fail)
    def unexpected(*args):
        pytest.fail('Incomplete batch must not write')
    monkeypatch.setattr(fetch_data, 'save_raw_data', unexpected)
    monkeypatch.setattr(fetch_data, 'upsert_to_postgres', unexpected)
    with pytest.raises(RuntimeError, match='Incomplete ticker batch'):
        fetch_data.main()
