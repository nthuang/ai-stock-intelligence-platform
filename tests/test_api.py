from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from backend import main
from backend.models import Base, StockPrice


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    with engine.begin() as connection:
        connection.execute(StockPrice.__table__.insert(), [
            dict(date=datetime(2026, 1, 1), ticker="AAPL", open=100, high=101, low=99,
                 close=100, volume=1000, daily_return_pct=None, signal=None),
            dict(date=datetime(2026, 1, 2), ticker="AAPL", open=100, high=105, low=99,
                 close=104, volume=1100, daily_return_pct=4, signal="BIG_GAIN"),
            dict(date=datetime(2026, 1, 2), ticker="MSFT", open=200, high=201, low=190,
                 close=192, volume=900, daily_return_pct=-4, signal="BIG_DROP"),
        ])
    with TestClient(main.app) as test_client:
        yield test_client
    engine.dispose()


def test_root_endpoint(client):
    assert client.get("/").json()["message"] == "AI Stock Intelligence API is running"


def test_null_indicators_and_sql_filters(client):
    response = client.get("/stocks?ticker=aapl&start=2026-01-01&end=2026-01-01")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["MA_30"] is None
    assert response.json()[0]["Daily Return %"] is None


def test_pagination_and_validation(client):
    assert client.get("/stocks?limit=1").json()[0]["Ticker"] == "AAPL"
    assert client.get("/stocks?limit=1&offset=1").json()[0]["Ticker"] == "MSFT"
    assert client.get("/stocks?limit=1001").status_code == 422
    assert client.get("/stocks?offset=-1").status_code == 422
    assert client.get("/stocks?start=2026-02-01&end=2026-01-01").status_code == 422
    assert client.get("/stocks?ticker=UNKNOWN").json() == []


def test_latest_signals_movers_and_tickers(client):
    assert len(client.get("/latest").json()) == 2
    assert len(client.get("/signals").json()) == 2
    assert len(client.get("/signals/latest").json()) == 2
    assert client.get("/tickers").json() == {"tickers": ["AAPL", "MSFT"]}
    movers = client.get("/top-movers").json()
    assert movers["top_gainers"][0]["Ticker"] == "AAPL"
    assert movers["top_losers"][0]["Ticker"] == "MSFT"


def test_stale_data_and_summary_fallback(client):
    health = client.get("/health").json()
    assert health["rows"] == 3
    assert health["stale"] is True
    response = client.get("/ai-summary/latest")
    assert response.status_code == 200
    assert response.json()["stale"] is True
    assert response.json()["llm_source"] == "fallback"


def test_empty_database(client):
    with main.engine.begin() as connection:
        connection.execute(StockPrice.__table__.delete())
    assert client.get("/stocks").json() == []
    assert client.get("/signals").json() == []
    assert client.get("/latest").status_code == 404
    assert client.get("/signals/latest").status_code == 404
    assert client.get("/health").json()["status"] == "degraded"


def test_no_signals_is_consistent_list(client):
    with main.engine.begin() as connection:
        connection.execute(StockPrice.__table__.update().values(signal="NORMAL"))
    assert client.get("/signals/latest").json() == []
    assert client.get("/ai-summary/latest").json()["llm_source"] == "fallback"


def test_database_error_does_not_leak_details(client, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError("password=secret", {}, Exception("private host"))
    monkeypatch.setattr(main.engine, "connect", fail)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database temporarily unavailable"}


def test_response_schemas_are_documented(client):
    schema = client.get("/openapi.json").json()
    assert "StockRecord" in schema["components"]["schemas"]
    assert "SummaryResponse" in schema["components"]["schemas"]


def test_normal_market_still_gets_ai_summary(client, monkeypatch):
    captured = []
    def summarize(rows):
        captured.extend(rows)
        return {'summary': 'Modest movement across the snapshot.', 'source': 'groq', 'model': 'test'}
    monkeypatch.setattr(main, 'generate_summary', summarize)
    with main.engine.begin() as connection:
        connection.execute(StockPrice.__table__.update().values(signal='NORMAL'))
    result = client.get('/ai-summary/latest').json()
    assert result['llm_source'] == 'groq'
    assert result['signals'] == []
    assert len(captured) == 2


def test_live_provider_failure_does_not_serve_stale_prices(client, monkeypatch):
    from fastapi import HTTPException
    def fail():
        raise HTTPException(status_code=503, detail='Live refresh unavailable')
    monkeypatch.setattr(main.market_refresh, 'ensure', fail)
    assert client.get('/latest').status_code == 503
    assert client.get('/ai-summary/latest').status_code == 503
