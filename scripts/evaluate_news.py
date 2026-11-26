"""Offline retrieval/guardrail benchmark. Makes no provider calls or factual-accuracy claim."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Set before importing modules that load dotenv. Never load developer credentials.
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
from backend import news, research

FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures/news_evaluation.json'


def evaluate():
    fixtures = json.loads(FIXTURES.read_text(encoding='utf-8'))
    retrieval, claims = [], []
    with patch.dict(os.environ, {'DATA_MODE': 'live', 'NEWS_ENABLED': 'true', 'TAVILY_API_KEY': 'synthetic-only',
                                 'NEWS_MAX_RESULTS': '5'}), \
         patch.object(news, 'rss_candidates', return_value={'status': 'ok', 'results': []}):
        for ticker, company in news.COMPANIES.items():
            for case in fixtures['retrieval']:
                article = {key: case[key].replace('{company}', company) for key in ('title', 'content', 'published_date')}
                article['url'] = case.get('url', 'https://example.com' + case.get('path', '/' + case['id']))
                with patch.object(news.TavilyClient, 'search', return_value={'results': [article]}):
                    result = news.search_stock_news(ticker, '2026-01-02')
                accepted = bool(result['articles'])
                coverage = result['articles'][0]['coverage'] if accepted else None
                retrieval.append({'id': ticker + ':' + case['id'], 'expected': case['expected'], 'accepted': accepted,
                                  'passed': accepted == case['expected'] and coverage == case.get('coverage')})
    for case in fixtures['claims']:
        source = {'id': 'S1', 'ticker': 'AAPL', 'title': 'Apple announces a new product',
                  'content': 'Apple announced a new product. No sales forecast was provided.',
                  'coverage': case.get('coverage', 'Company event')}
        if source['coverage'] == 'Holdings report':
            source.update(title='Fund sold Apple shares', content='A filing described trades from the previous quarter.')
        source['content'] = case.get('source_content', source['content'])
        payload = {'explanations': [{'source_id': case.get('source_id', 'S1'), 'possible_influence': case['claim'],
                                     'topic': case.get('topic'), 'evidence_quote': case.get('evidence_quote')}]}
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
        with patch.object(research.ai_summary, '_get_client', return_value=client):
            result = research.grounded_narrative([], [source], 'Explain Apple news')
        accepted = bool(result['explanations'])
        claims.append({'id': case['id'], 'claim': case['claim'], 'source': source,
                       'model_selection': payload['explanations'][0], 'displayed_explanations': result['explanations'],
                       'accepted': accepted, 'supported_by_fixture': case['supported'],
                       'guardrail_expectation_met': accepted == case['allow']})
    unsupported = [item for item in claims if not item['supported_by_fixture']]
    tp = sum(r['accepted'] and r['expected'] for r in retrieval)
    accepted = sum(r['accepted'] for r in retrieval)
    expected = sum(r['expected'] for r in retrieval)
    return {'benchmark': 'synthetic-news-policy-v2', 'external_calls': 0,
            'scope': fixtures['description'],
            'metrics': {'retrieval_cases': len(retrieval), 'retrieval_scenarios': len(fixtures['retrieval']),
                        'retrieval_passed': sum(r['passed'] for r in retrieval),
                        'fixture_precision': tp / accepted if accepted else None,
                        'fixture_recall': tp / expected if expected else None,
                        'claim_probes': len(claims),
                        'guardrail_expectations_met': sum(c['guardrail_expectation_met'] for c in claims),
                        'unsupported_probe_count': len(unsupported),
                        'unsupported_probes_accepted': sum(c['accepted'] for c in unsupported)},
            'limitations': ['Authored examples, not measured live-provider accuracy.',
                            'Exact excerpt membership does not establish the truth or completeness of a report.',
                            'Commentary is server-authored topic context, not an event-specific causal explanation.'],
            'retrieval': retrieval, 'claim_review': claims}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true', help='Fail on policy regressions; semantic gaps are reported separately.')
    args = parser.parse_args()
    report = evaluate()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['metrics'], indent=2))
    if args.check and (not all(r['passed'] for r in report['retrieval']) or
                       not all(c['guardrail_expectation_met'] for c in report['claim_review'])):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
