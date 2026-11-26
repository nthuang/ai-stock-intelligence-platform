"""Small, bounded process counters; persistent state stays in PostgreSQL."""
from collections import Counter
from datetime import datetime, timezone
import json
import logging
from threading import Lock

_lock = Lock()
_counts = Counter()
_started = datetime.now(timezone.utc).isoformat()
logger = logging.getLogger('uvicorn.error')


def record(event, *, duration_ms=None):
    # Callers supply fixed event names, never URLs, questions, keys or recipients.
    with _lock:
        _counts[event] += 1
        if duration_ms is not None:
            _counts[event + '_total_ms'] += round(duration_ms, 3)
    logger.info(json.dumps({'event': event, 'duration_ms': duration_ms}))


def snapshot():
    with _lock:
        return {'scope': 'this API process; resets on restart', 'started_at': _started,
                'counters': dict(_counts)}
