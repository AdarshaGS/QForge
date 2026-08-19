"""In-process runtime performance registry (issue #43).

Shared by every live instrumentation call site (query execution, db
connect/switch, schema load, autocomplete, result-grid render) and by the
developer performance overlay (ui/perf_overlay.py) that reads it back. Not
persisted — this is live, in-memory, per-process state, distinct from
benchmarks/ (which runs standalone timed scripts and persists history to
disk). Module-level functions, matching the style of utils/schema_cache.py.
"""

from __future__ import annotations

import statistics
import threading
from collections import deque

_LOCK = threading.Lock()
_SAMPLES: dict[tuple[str, str], deque] = {}
_COUNTERS: dict[str, dict[str, int]] = {}

_MAX_SAMPLES = 50


def record(category: str, name: str, value: float) -> None:
    """Record one sample for category/name — usually a duration in ms
    (e.g. "database", "query_execute"), but any numeric series works
    (e.g. "result_grid", "rows_rendered")."""
    key = (category, name)
    with _LOCK:
        samples = _SAMPLES.get(key)
        if samples is None:
            samples = deque(maxlen=_MAX_SAMPLES)
            _SAMPLES[key] = samples
        samples.append(value)


def counter_inc(name: str, bucket: str) -> None:
    """Increment a named counter bucket (e.g. counter_inc("schema_cache", "hit"))."""
    with _LOCK:
        buckets = _COUNTERS.setdefault(name, {})
        buckets[bucket] = buckets.get(bucket, 0) + 1


def counter_get(name: str) -> dict[str, int]:
    with _LOCK:
        return dict(_COUNTERS.get(name, {}))


def snapshot() -> dict[str, dict[str, dict]]:
    """category -> name -> {last, mean, p95, count}, for the overlay to poll."""
    with _LOCK:
        items = [(cat, name, list(samples)) for (cat, name), samples in _SAMPLES.items()]

    result: dict[str, dict[str, dict]] = {}
    for cat, name, values in items:
        if not values:
            continue
        sorted_vals = sorted(values)
        p95_idx = max(0, int(len(sorted_vals) * 0.95) - 1)
        result.setdefault(cat, {})[name] = {
            "last": values[-1],
            "mean": statistics.mean(values),
            "p95": sorted_vals[p95_idx],
            "count": len(values),
        }
    return result


def reset() -> None:
    """Clear all recorded samples and counters. Test-only."""
    with _LOCK:
        _SAMPLES.clear()
        _COUNTERS.clear()
