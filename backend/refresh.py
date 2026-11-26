"""Bounded live refresh shared by requests and the background scheduler."""
from datetime import datetime, timezone
import logging
import os
from threading import Lock
import time

import pandas as pd
from fastapi import HTTPException
from backend.market_calendar import market_state

logger = logging.getLogger(__name__)


def fetch_and_store() -> None:
    from scripts.fetch_data import add_indicators, fetch_ticker_data, upsert_to_postgres
    from backend.db import engine
    from backend.universe import ingestion_tickers, BENCHMARKS
    # Fetch the complete universe before writing anything. Do not overwrite demo fixtures.
    frames = [fetch_ticker_data(ticker) for ticker in ingestion_tickers(engine) if ticker not in BENCHMARKS]
    latest_dates = {frame['Date'].max() for frame in frames}
    if len(latest_dates) != 1:
        raise ValueError('Provider returned inconsistent latest sessions')
    upsert_to_postgres(add_indicators(pd.concat(frames, ignore_index=True)))
    # An unavailable benchmark must not block the monitored equity pipeline.
    for ticker in BENCHMARKS:
        try:
            upsert_to_postgres(add_indicators(fetch_ticker_data(ticker)))
        except Exception as exc:
            logger.warning('Benchmark refresh failed for %s: %s', ticker, type(exc).__name__)


class MarketRefresh:
    def __init__(self):
        self.lock = Lock()
        self.last_success = None
        self.last_attempt = None
        self.success_clock = None
        self.attempt_clock = None
        self.error = None
        self.duration_seconds = None
        self.failures = 0

    @property
    def live(self):
        return os.getenv('DATA_MODE', 'live') == 'live'

    @property
    def interval(self):
        configured = max(10, int(os.getenv('REFRESH_INTERVAL_SECONDS', '60')))
        # Allow delayed closing bars to settle. Closed-market polling is hourly.
        return configured if market_state()['refresh_window'] else max(3600, configured)

    def status(self):
        return {'mode': 'live' if self.live else 'demo',
                'refresh_interval_seconds': self.interval,
                'last_successful_fetch': self.last_success,
                'last_refresh_attempt': self.last_attempt,
                'refresh_error': self.error,
                'last_ingestion_duration_seconds': self.duration_seconds,
                'provider_failures_since_start': self.failures}

    def ensure(self):
        if not self.live:
            return
        with self.lock:
            now = time.monotonic()
            if self.error is None and self.success_clock is not None and now - self.success_clock < self.interval:
                return
            retry_interval = min(30, self.interval) if market_state()['refresh_window'] else self.interval
            if self.error is not None and self.attempt_clock is not None and now - self.attempt_clock < retry_interval:
                raise HTTPException(status_code=503, detail=self.error)
            self.attempt_clock = now
            self.last_attempt = datetime.now(timezone.utc).isoformat()
            try:
                fetch_and_store()
            except Exception as exc:
                self.failures += 1
                self.duration_seconds = round(time.monotonic() - now, 3)
                logger.warning('Live refresh failed: %s', type(exc).__name__)
                self.error = 'Live market refresh failed; retry shortly. Stored data has not been presented as fresh.'
                raise HTTPException(status_code=503, detail=self.error) from None
            self.last_success = datetime.now(timezone.utc).isoformat()
            self.success_clock = time.monotonic()
            self.duration_seconds = round(self.success_clock - now, 3)
            self.error = None


market_refresh = MarketRefresh()
