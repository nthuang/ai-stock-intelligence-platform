"""Verify a running demo over HTTP, without calling a paid summary provider."""
import argparse
import json
from urllib.request import urlopen


def check(base_url: str) -> None:
    def get(path):
        with urlopen(base_url.rstrip('/') + path, timeout=10) as response:
            assert response.status == 200
            return json.load(response)

    health = get('/health')
    assert health['rows'] >= 620
    assert len(get('/tickers')['tickers']) == 5
    assert len(get('/latest')) == 5
    rows = get('/stocks?ticker=AAPL&limit=2')
    assert len(rows) == 2 and all(row['Ticker'] == 'AAPL' for row in rows)
    assert get('/stocks?limit=1&offset=1') != get('/stocks?limit=1')
    warmup = get('/stocks?ticker=AAPL&start=2025-10-27&end=2025-10-27')
    assert warmup[0]['MA_30'] is None
    assert 'StockRecord' in get('/openapi.json')['components']['schemas']
    with urlopen(base_url.rstrip('/') + '/dashboard', timeout=10) as response:
        assert b'Market Observatory' in response.read()
    print('Demo smoke checks passed: ingestion, pagination, nulls, schemas, dashboard')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8000')
    check(parser.parse_args().base_url)
