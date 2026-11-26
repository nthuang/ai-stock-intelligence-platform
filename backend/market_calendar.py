"""NASDAQ cash-session boundaries, including holidays and scheduled early closes."""
from datetime import datetime, time, date, timezone, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

MARKET_TIMEZONE = ZoneInfo('America/New_York')


def market_state(now=None):
    """Read the exchange calendar only; never contact a quote provider."""
    now = now or datetime.now(timezone.utc)
    local_date = now.astimezone(MARKET_TIMEZONE).date()
    bounds = session_bounds(local_date.isoformat())
    active = bool(bounds and bounds[0] <= now <= bounds[1] + timedelta(minutes=30))
    latest = None
    # Include the preceding year for the first sessions of January.
    for year in (local_date.year, local_date.year - 1):
        closed = year_schedule(year)
        closed = closed[closed.market_close <= now]
        if not closed.empty:
            latest = closed.index[-1].date().isoformat()
            break
    return {'exchange': 'NASDAQ', 'timezone': str(MARKET_TIMEZONE),
            'market_date': local_date.isoformat(),
            'phase': 'open' if bounds and bounds[0] <= now < bounds[1] else 'closed',
            'refresh_window': active, 'latest_completed_session': latest}


@lru_cache(maxsize=16)
def year_schedule(year):
    return mcal.get_calendar('NASDAQ').schedule(f'{year}-01-01', f'{year}-12-31')


def session_bounds(market_date):
    day = date.fromisoformat(market_date)
    schedule = year_schedule(day.year)
    if market_date not in schedule.index:
        return None
    row = schedule.loc[market_date]
    return row.market_open.to_pydatetime(), row.market_close.to_pydatetime()


def session_close(market_date):
    bounds = session_bounds(market_date)
    # No session: cutoff at local midnight excludes all same-day reports.
    return bounds[1] if bounds else datetime.combine(
        date.fromisoformat(market_date), time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
