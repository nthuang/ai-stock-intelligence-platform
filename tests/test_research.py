import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from backend import research
from backend.models import Base, AlertDelivery
from backend.notifications import claim_delivery, complete_delivery, render_report


SOURCES = [{'id': 'S1', 'ticker': 'AAPL', 'title': 'Apple earnings', 'content': 'Apple reported earnings.',
            'url': 'https://example.com/apple', 'publisher': 'example.com', 'published_date': '2026-01-02T12:00:00Z'}]


@pytest.mark.parametrize('payload,expected', [
    ({'overview': 'Apple moved amid earnings reports.', 'explanations': [{'source_id': 'S1', 'possible_influence': 'Earnings may influence expectations.'}]}, 'groq'),
    ({'overview': 'A report.', 'explanations': [{'source_id': 'S99', 'possible_influence': 'Could matter.'}]}, 'fallback'),
    ({'overview': 'Visit https://invented.example', 'explanations': []}, 'fallback'),
    ({'overview': 'AAPL gained 99%.', 'explanations': []}, 'fallback'),
    ({'overview': 'See [S99].', 'explanations': []}, 'fallback'),
    ({'overview': None, 'explanations': []}, 'fallback'),
])
def test_citation_and_numeric_validation(monkeypatch, payload, expected):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])
    create = Mock(return_value=response)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(research.ai_summary, '_get_client', lambda: fake)
    result = research.grounded_narrative([], SOURCES, 'Why Apple?')
    assert result['source'] == expected
    prompt = create.call_args.kwargs['messages']
    assert 'untrusted' in prompt[0]['content']
    assert 'https://example.com/apple' not in prompt[1]['content']
    if expected == 'groq':
        assert result['explanations'][0]['reported_event'] == 'Apple earnings'


def test_news_targets_prioritize_signals_and_bound_searches(monkeypatch):
    rows = [{'Ticker': ticker, 'Date': '2026-01-02', 'Daily Return %': change, 'Signal': signal}
            for ticker, change, signal in [('AAPL', 5, 'BIG_GAIN'), ('NVDA', -7, 'BIG_DROP'), ('MSFT', 1, 'NORMAL')]]
    lookup = Mock(return_value={'status': 'no_results', 'articles': [], 'cache_hit': False})
    monkeypatch.setattr(research, 'get_news', lookup)
    monkeypatch.setenv('NEWS_MAX_TICKERS_PER_REQUEST', '1')
    sources, statuses = research.retrieve_context(None, rows, 'market', summary=True)
    assert lookup.call_count == 1
    assert lookup.call_args.args[1] == 'NVDA'
    assert statuses['AAPL']['status'] == 'request_limit'
    assert 'MSFT' not in statuses


def test_delivery_retries_and_deduplication(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'delivery.db'))
    Base.metadata.create_all(engine)
    try:
        first = claim_delivery(engine, '2026-01-02', 'test@example.com')
        assert first['allowed']
        assert claim_delivery(engine, '2026-01-02', 'test@example.com')['reason'] == 'pending'
        # Failed Gmail send never reaches completion. Its lease expires for a retry.
        with engine.begin() as connection:
            connection.execute(AlertDelivery.__table__.update().values(expires_at=0))
        retry = claim_delivery(engine, '2026-01-02', 'test@example.com')
        assert retry['allowed'] and retry['token'] != first['token']
        assert not complete_delivery(engine, first['key'], first['token'])
        assert complete_delivery(engine, retry['key'], retry['token'])
        assert complete_delivery(engine, retry['key'], retry['token'])
        assert claim_delivery(engine, '2026-01-02', 'TEST@example.com')['reason'] == 'already_sent'
        assert claim_delivery(engine, '2026-01-03', 'test@example.com')['allowed']
    finally:
        engine.dispose()


def test_email_html_escapes_untrusted_content():
    payload = {'date': '2026-01-02', 'market_overview': '<script>alert(1)</script>', 'performance': [],
               'sources': [{**SOURCES[0], 'title': '<img src=x onerror=alert(1)>', 'url': 'https://example.com/?a="bad"'},
                           {**SOURCES[0], 'url': 'javascript:alert(1)'}],
               'explanations': [{'source_id': 'S1', 'reported_event': '<b>bad</b>', 'possible_influence': '<svg/onload=x>'}],
               'news_status': 'ok', 'llm_source': 'groq', 'model': 'test', 'stale': False}
    html = render_report(payload)
    assert '<script>' not in html and '<img' not in html and '<svg' not in html
    assert '&lt;script&gt;' in html and '&quot;bad&quot;' in html
    assert 'javascript:' not in html
    assert 'not investment advice' in html


def test_workflow_records_only_after_success_and_has_no_credentials():
    workflow = json.loads(Path('automation/n8n_workflow.json').read_text(encoding='utf-8-sig'))
    nodes = {node['name']: node for node in workflow['nodes']}
    assert all('credentials' not in node for node in nodes.values())
    assert workflow['active'] is False
    assert workflow['connections']['Send a message']['main'][0][0]['node'] == 'Record delivery'
    assert not nodes['Send a message'].get('continueOnFail', False)
    assert nodes['Send a message']['parameters']['sendTo'] == '={{$env.ALERT_EMAIL}}'
    assert nodes['Record delivery']['retryOnFail'] is True
    assert len(nodes['If']['parameters']['conditions']['conditions']) == 2


def test_cached_or_mocked_old_news_is_rejected_at_research_boundary(monkeypatch):
    rows = [{'Ticker': 'AAPL', 'Date': '2026-01-02', 'Daily Return %': 4, 'Signal': 'BIG_GAIN'}]
    monkeypatch.setattr(research, 'get_news', lambda *args, **kwargs: {
        'status': 'ok', 'cache_hit': True, 'articles': [{**SOURCES[0], 'published_date': '2026-01-01T12:00:00Z'}]})
    sources, statuses = research.retrieve_context(None, rows, 'why apple up')
    assert sources == []
    assert statuses['AAPL']['status'] == 'no_results'


def test_generated_overview_cannot_invert_computed_trend(monkeypatch):
    payload = {'overview': 'The short-term average was above price and long-term average below.',
               'explanations': [{'source_id': 'S1', 'possible_influence': 'Earnings may influence expectations.'}]}
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
    monkeypatch.setattr(research.ai_summary, '_get_client', lambda: fake)
    result = research.grounded_narrative([], SOURCES, 'why apple up')
    assert result['source'] == 'groq'
    assert 'average was above' not in result['summary']
    assert 'Same-session reports' in result['summary']
