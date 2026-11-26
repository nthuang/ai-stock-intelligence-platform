from fastapi.testclient import TestClient

from backend import access, main


def test_notification_endpoints_fail_closed(monkeypatch):
    monkeypatch.delenv('NOTIFICATION_API_KEY', raising=False)
    client = TestClient(main.app)
    for path in ('claim', 'complete'):
        assert client.post('/notifications/' + path, json={}).status_code == 403
    monkeypatch.setenv('NOTIFICATION_API_KEY', 'private-test-key')
    assert client.post('/notifications/claim', json={}, headers={'X-Notification-Key': 'wrong'}).status_code == 403
    assert client.post('/notifications/claim', json={}, headers={'X-Notification-Key': 'private-test-key'}).status_code == 422
    monkeypatch.setenv('NOTIFICATION_API_KEY', 'non-ascii-\u00e9')
    assert client.post('/notifications/claim', json={}, headers={'X-Notification-Key': 'wrong'}).status_code == 403


def test_public_demo_cannot_enable_live_or_notifications(monkeypatch):
    monkeypatch.setenv('DEPLOYMENT_MODE', 'public_demo')
    monkeypatch.setenv('DATA_MODE', 'live')
    client = TestClient(main.app)
    assert client.get('/').status_code == 503
    monkeypatch.setenv('DATA_MODE', 'demo')
    monkeypatch.setenv('NOTIFICATION_API_KEY', 'private-test-key')
    assert client.post('/notifications/claim', headers={'X-Notification-Key': 'private-test-key'}, json={}).status_code == 403


def test_demo_rate_limit_is_bounded_and_recovers(monkeypatch):
    clock = [6000]
    monkeypatch.setattr(access.time, 'monotonic', lambda: clock[0])
    for _ in range(30):
        assert access.demo_request_allowed('test-ip')
    assert not access.demo_request_allowed('test-ip')
    clock[0] += 60
    assert access.demo_request_allowed('test-ip')
    for i in range(599):
        assert access.demo_request_allowed(str(i))
    assert not access.demo_request_allowed('new-address')
    assert len(access._buckets) <= 600
