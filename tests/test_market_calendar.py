from datetime import datetime, timezone

from backend import news, research
from backend.market_calendar import session_bounds, session_close, market_state


def test_exchange_holidays_early_closes_and_dst():
    assert session_bounds('2026-11-26') is None
    assert session_bounds('2026-11-28') is None
    assert session_close('2026-11-27').isoformat() == '2026-11-27T18:00:00+00:00'
    assert session_close('2025-07-03').isoformat() == '2025-07-03T17:00:00+00:00'
    assert session_close('2026-07-02').isoformat() == '2026-07-02T20:00:00+00:00'
    assert session_close('2026-01-02').hour == 21
    assert session_close('2026-06-01').hour == 20


def test_early_close_and_non_session_articles_are_rejected():
    # Historical early close avoids depending on the machine's current date.
    assert news.session_article_date('2025-11-28T18:00:00Z', '2025-11-28') == '2025-11-28'
    assert news.session_article_date('2025-11-28T18:00:01Z', '2025-11-28') is None
    assert news.session_article_date('2025-11-27T12:00:00Z', '2025-11-27') is None


def test_cached_news_is_filtered_against_persisted_price_observation(monkeypatch):
    row = {'Ticker': 'AAPL', 'Date': '2026-01-02', 'Daily Return %': 1,
           'Observed At': '2026-01-02T15:00:00+00:00'}
    articles = [{'title': 'Apple news', 'url': f'https://example.com/{hour}',
                 'published_date': f'2026-01-02T{hour}:00:00Z'} for hour in ('14', '16')]
    monkeypatch.setattr(research, 'get_news', lambda *args: {'status': 'ok', 'articles': articles})
    sources, _ = research.retrieve_context(None, [row], 'news')
    assert [s['url'] for s in sources] == ['https://example.com/14']


def test_market_refresh_window_holiday_early_close_and_year_boundary():
    thanksgiving = market_state(datetime.fromisoformat('2026-11-26T17:00:00+00:00'))
    assert thanksgiving['phase'] == 'closed'
    assert thanksgiving['refresh_window'] is False
    assert thanksgiving['latest_completed_session'] == '2026-11-25'
    early = market_state(datetime.fromisoformat('2026-11-27T18:15:00+00:00'))
    assert early['phase'] == 'closed' and early['refresh_window'] is True
    assert not market_state(datetime.fromisoformat('2026-11-27T18:31:00+00:00'))['refresh_window']
    assert market_state(datetime.fromisoformat('2026-01-01T17:00:00+00:00'))['latest_completed_session'] == '2025-12-31'
