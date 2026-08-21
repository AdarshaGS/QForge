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
import time
from collections import deque

_LOCK = threading.Lock()
_SAMPLES: dict[tuple[str, str], deque] = {}
_COUNTERS: dict[str, dict[str, int]] = {}
_ACTIVE_TASKS: dict[str, int] = {}
_SUSPEND_WINDOWS: list[tuple[float, float]] = []
_WATCHDOG_STARTED = False

_MAX_SAMPLES = 50
_WATCHDOG_INTERVAL_S = 1.0
_SUSPEND_GAP_THRESHOLD_S = 3.0
_MAX_SUSPEND_WINDOWS = 20


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


def task_started(name: str) -> None:
    """Mark one background operation of *name* (e.g. "schema_fetch",
    "export") as started — a live in-flight gauge, not a rolling sample
    (issue #171). Always pair with task_finished() in a try/finally."""
    with _LOCK:
        _ACTIVE_TASKS[name] = _ACTIVE_TASKS.get(name, 0) + 1


def task_finished(name: str) -> None:
    with _LOCK:
        _ACTIVE_TASKS[name] = max(0, _ACTIVE_TASKS.get(name, 0) - 1)


def active_tasks() -> dict[str, int]:
    """name -> count of currently in-flight operations, omitting any name
    whose count has dropped back to zero."""
    with _LOCK:
        return {k: v for k, v in _ACTIVE_TASKS.items() if v > 0}


def start_suspend_watchdog() -> None:
    """Idempotent — safe to call from multiple entry points. Spawns a
    daemon thread that does nothing but sleep in a loop and check how long
    each sleep actually took. A thread with no work has no legitimate
    reason to wake up late; if it does, the whole process (not just one
    busy thread) was almost certainly suspended for that stretch — system
    sleep, macOS App Nap, a debugger pause. Recording those windows lets a
    slow-looking sample get checked against them: "the machine was asleep"
    and "this operation was genuinely slow" produce identical elapsed-time
    numbers from inside the stalled process, and only an independent,
    otherwise-idle clock can tell them apart (issue #174)."""
    global _WATCHDOG_STARTED
    with _LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def _loop():
        last = time.time()
        while True:
            time.sleep(_WATCHDOG_INTERVAL_S)
            now = time.time()
            gap = now - last
            if gap > _SUSPEND_GAP_THRESHOLD_S:
                with _LOCK:
                    _SUSPEND_WINDOWS.append((last, now))
                    del _SUSPEND_WINDOWS[:-_MAX_SUSPEND_WINDOWS]
            last = now

    threading.Thread(target=_loop, daemon=True, name="perf_metrics_suspend_watchdog").start()


def likely_suspended_between(start_ts: float, end_ts: float) -> bool:
    """True if a detected suspend window overlaps [start_ts, end_ts] —
    both time.time() epoch seconds, e.g. captured immediately before/after
    the operation being measured (not perf_counter(), which isn't epoch-
    relative and can't be compared against the watchdog's clock)."""
    with _LOCK:
        windows = list(_SUSPEND_WINDOWS)
    return any(w_start < end_ts and start_ts < w_end for w_start, w_end in windows)


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
    """Clear all recorded samples and counters. Test-only. Does not stop
    the suspend watchdog thread (if started) — it has no per-test state
    worth tearing down, just clears its recorded windows."""
    with _LOCK:
        _SAMPLES.clear()
        _COUNTERS.clear()
        _ACTIVE_TASKS.clear()
        _SUSPEND_WINDOWS.clear()
