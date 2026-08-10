"""Minimal timing harness for QForge performance benchmarks (issue #42).

No new dependency: stdlib time.perf_counter + statistics is enough for
latency numbers. Add psutil later only if memory/CPU benchmarks are in scope.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
HISTORY_FILE = RESULTS_DIR / "history.jsonl"


@dataclass
class BenchResult:
    name: str
    iterations: int
    mean_ms: float
    median_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float


def run_bench(name: str, fn, iterations: int = 5, warmup: int = 1) -> BenchResult:
    """Time `fn()` `iterations` times (plus `warmup` untimed calls) and summarize."""
    for _ in range(warmup):
        fn()

    samples_ms = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples_ms.append((time.perf_counter() - start) * 1000)

    return BenchResult(
        name=name,
        iterations=iterations,
        mean_ms=statistics.mean(samples_ms),
        median_ms=statistics.median(samples_ms),
        min_ms=min(samples_ms),
        max_ms=max(samples_ms),
        stdev_ms=statistics.stdev(samples_ms) if len(samples_ms) > 1 else 0.0,
    )


def save_results(results: list[BenchResult], timestamp: str) -> None:
    """Append one JSON line per run set to benchmarks/results/history.jsonl."""
    RESULTS_DIR.mkdir(exist_ok=True)
    record = {"timestamp": timestamp, "results": [asdict(r) for r in results]}
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
