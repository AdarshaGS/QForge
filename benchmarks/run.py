"""CLI entry point: run all registered benchmark modules and record results.

Usage: python -m benchmarks.run
"""

from __future__ import annotations

import time

from benchmarks import bench_db_service, bench_startup, bench_connection_switching
from benchmarks.harness import save_results

# Add each new bench_*.py module's `benchmarks()` function here as it's built.
MODULES = [bench_db_service, bench_startup, bench_connection_switching]


def main() -> None:
    all_results = []
    for module in MODULES:
        all_results.extend(module.benchmarks())

    for r in all_results:
        print(
            f"{r.name}: cold={r.cold_ms:.3f}ms median={r.median_ms:.3f}ms "
            f"p95={r.p95_ms:.3f}ms p99={r.p99_ms:.3f}ms mem={r.mem_delta_kb:+.0f}KB "
            f"cpu={r.cpu_ms:.3f}ms (n={r.iterations})"
        )

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_results(all_results, timestamp)
    print(f"\nSaved to benchmarks/results/history.jsonl @ {timestamp}")


if __name__ == "__main__":
    main()
