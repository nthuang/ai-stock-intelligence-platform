"""Bounded demo traffic and private notification controls. No public live mode."""
from collections import OrderedDict
import hmac
import logging
import os
from threading import Lock
import time

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
_lock = Lock()
_buckets = OrderedDict()
_total = [0, 0]


def demo_request_allowed(address):
    minute = int(time.monotonic() // 60)
    with _lock:
        if _total[0] != minute:
            _total[:] = [minute, 0]
            _buckets.clear()
        count = _buckets.get(address, 0)
        if count >= 30 or _total[1] >= 600:
            return False
        _buckets[address] = count + 1
        _total[1] += 1
        return True


async def access_control(request, call_next):
    started = time.monotonic()
    mode = os.getenv('DEPLOYMENT_MODE', 'local')
    if mode not in {'local', 'public_demo'} or (mode == 'public_demo' and os.getenv('DATA_MODE') != 'demo'):
        return JSONResponse(status_code=503, content={'detail': 'Invalid deployment mode configuration'})
    if request.url.path.startswith('/notifications/'):
        configured = os.getenv('NOTIFICATION_API_KEY', '')
        supplied = request.headers.get('X-Notification-Key', '')
        if mode == 'public_demo' or not configured or not hmac.compare_digest(configured, supplied):
            return JSONResponse(status_code=403, content={'detail': 'Notification access denied'})
    if mode == 'public_demo' and request.url.path != '/health':
        # Ignore client-supplied forwarding headers. Behind a proxy this deliberately
        # shares one limit unless trusted proxy handling is configured by the operator.
        address = request.client.host if request.client else 'unknown'
        if not demo_request_allowed(address):
            return JSONResponse(status_code=429, headers={'Retry-After': '60'},
                                content={'detail': 'Demo request limit reached; retry shortly'})
    response = await call_next(request)
    route = request.scope.get('route')
    logger.info('request route=%s status=%d duration_ms=%.1f',
                getattr(route, 'path', 'unmatched'), response.status_code,
                (time.monotonic() - started) * 1000)
    return response
