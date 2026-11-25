"""Bounded live refresh shared by requests and the background scheduler."""
from datetime import datetime, timezone
import logging
import os
from threading import Lock
import time

import pandas as pd
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def fetch_and_store() -> None:
    from scripts.fetch_data import TICKERS, add_indicators, fetch_ticker_data, upsert_to_postgres
    # Fetch the complete universe before writing anything. Do not overwrite demo fixtures.
    frames = [fetch_ticker_data(ticker) for ticker in TICKERS]
    latest_dates = {frame['Date'].max() for frame in frames}
    if len(latest_dates) != 1:
        raise ValueError('Provider returned inconsistent latest sessions')
    upsert_to_postgres(add_indicators(pd.concat(frames, ignore_index=True)))


class MarketRefresh:
    def __init__(self):
        self.lock = Lock()
        self.last_success = None
        self.last_attempt = None
        self.success_clock = None
        self.attempt_clock = None
        self.error = None

    @property
    def live(self):
        return os.getenv('DATA_MODE', 'live') == 'live'

    @property
    def interval(self):
        return max(10, int(os.getenv('REFRESH_INTERVAL_SECONDS', '60')))

    def status(self):
        return {'mode': 'live' if self.live else 'demo',
                'refresh_interval_seconds': self.interval,
                'last_successful_fetch': self.last_success,
                'last_refresh_attempt': self.last_attempt,
                'refresh_error': self.error}

    def ensure(self):
        if not self.live:
            return
        with self.lock:
            now = time.monotonic()
            if self.error is None and self.success_clock is not None and now - self.success_clock < self.interval:
                return
            if self.error is not None and self.attempt_clock is not None and now - self.attempt_clock < min(30, self.interval):
                raise HTTPException(status_code=503, detail=self.error)
            self.attempt_clock = now
            self.last_attempt = datetime.now(timezone.utc).isoformat()
            try:
                fetch_and_store()
            except Exception as exc:
                logger.warning('Live refresh failed: %s', type(exc).__name__)
                self.error = 'Live market refresh failed; retry shortly. Stored data has not been presented as fresh.'
                raise HTTPException(status_code=503, detail=self.error) from None
            self.last_success = datetime.now(timezone.utc).isoformat()
            self.success_clock = time.monotonic()
            self.error = None


market_refresh = MarketRefresh()
