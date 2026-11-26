"""NASDAQ cash-session boundaries, including holidays and scheduled early closes."""
from datetime import datetime, time, date, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

MARKET_TIMEZONE = ZoneInfo('America/New_York')


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
