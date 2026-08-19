"""Minimal timing harness for QForge performance benchmarks (issue #58).

No new dependency: stdlib time.perf_counter + statistics for latency, and
resource.getrusage for memory/CPU, is enough — psutil isn't installed and
isn't needed for process-level numbers.
"""

from __future__ import annotations

import json
import resource
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
HISTORY_FILE = RESULTS_DIR / "history.jsonl"


def _maxrss_kb(usage: "resource.struct_rusage") -> float:
    """ru_maxrss is KB on Linux but bytes on macOS — normalize to KB."""
    return usage.ru_maxrss / 1024 if sys.platform == "darwin" else float(usage.ru_maxrss)


@dataclass
class BenchResult:
    name: str
    iterations: int
    cold_ms: float
    mean_ms: float
    median_ms: float
    min_ms: float
    max_ms: float
    p95_ms: float
    p99_ms: float
    stdev_ms: float
    mem_delta_kb: float
    cpu_ms: float


def percentile(sorted_samples: list[float], pct: float) -> float:
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    idx = max(0, min(len(sorted_samples) - 1, int(round(pct * (len(sorted_samples) - 1)))))
    return sorted_samples[idx]


def summarize(
    name: str,
    samples_ms: list[float],
    cold_ms: float,
    mem_delta_kb: float = 0.0,
    cpu_ms: float = 0.0,
) -> BenchResult:
    """Build a BenchResult from an already-collected warm-sample list plus a
    separately-measured cold sample. Used by run_bench (fn timed directly)
    and by benchmarks that instead read elapsed times back out of
    utils/perf_metrics (e.g. bench_startup.py, bench_connection_switching.py)
    — same stats, two different ways of collecting the raw samples."""
    sorted_samples = sorted(samples_ms)
    return BenchResult(
        name=name,
        iterations=len(samples_ms),
        cold_ms=cold_ms,
        mean_ms=statistics.mean(samples_ms),
        median_ms=statistics.median(samples_ms),
        min_ms=min(samples_ms),
        max_ms=max(samples_ms),
        p95_ms=percentile(sorted_samples, 0.95),
        p99_ms=percentile(sorted_samples, 0.99),
        stdev_ms=statistics.stdev(samples_ms) if len(samples_ms) > 1 else 0.0,
        mem_delta_kb=mem_delta_kb,
        cpu_ms=cpu_ms,
    )


def run_bench(name: str, fn, iterations: int = 5, warmup: int = 1) -> BenchResult:
    """Time `fn()` once untimed-but-recorded as `cold_ms` (issue #58's cold
    vs warm distinction: first call pays import/disk-cache/connection-setup
    costs a steady-state loop never sees), `warmup` further untimed calls,
    then `iterations` timed calls whose stats become the warm numbers."""
    rusage_before = resource.getrusage(resource.RUSAGE_SELF)

    cold_start = time.perf_counter()
    fn()
    cold_ms = (time.perf_counter() - cold_start) * 1000

    for _ in range(warmup):
        fn()

    samples_ms = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples_ms.append((time.perf_counter() - start) * 1000)

    rusage_after = resource.getrusage(resource.RUSAGE_SELF)
    mem_delta_kb = _maxrss_kb(rusage_after) - _maxrss_kb(rusage_before)
    cpu_ms = (
        (rusage_after.ru_utime + rusage_after.ru_stime)
        - (rusage_before.ru_utime + rusage_before.ru_stime)
    ) * 1000

    return summarize(name, samples_ms, cold_ms, mem_delta_kb, cpu_ms)


def save_results(results: list[BenchResult], timestamp: str) -> None:
    """Append one JSON line per run set to benchmarks/results/history.jsonl."""
    RESULTS_DIR.mkdir(exist_ok=True)
    record = {"timestamp": timestamp, "results": [asdict(r) for r in results]}
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def compare_to_previous(name: str) -> dict | None:
    """Diff `name`'s latest recorded median_ms against its previous run in
    history.jsonl (issue #58's "baseline storage" — a comparison, not just
    an append-only log). Returns None if there's no prior run to compare."""
    if not HISTORY_FILE.exists():
        return None

    medians = []
    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            for r in record["results"]:
                if r["name"] == name:
                    medians.append((record["timestamp"], r["median_ms"]))

    if len(medians) < 2:
        return None

    (_, previous_median), (latest_ts, latest_median) = medians[-2], medians[-1]
    delta_ms = latest_median - previous_median
    delta_pct = (delta_ms / previous_median * 100) if previous_median else 0.0
    return {
        "name": name,
        "timestamp": latest_ts,
        "latest_median_ms": latest_median,
        "previous_median_ms": previous_median,
        "delta_ms": delta_ms,
        "delta_pct": delta_pct,
    }
