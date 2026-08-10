from benchmarks.harness import run_bench


def test_run_bench_computes_summary_stats():
    calls = []
    result = run_bench("noop", lambda: calls.append(1), iterations=5, warmup=2)
    assert len(calls) == 7  # 2 warmup + 5 timed
    assert result.iterations == 5
    assert result.min_ms <= result.median_ms <= result.max_ms
    assert result.mean_ms >= 0


def test_run_bench_single_iteration_has_zero_stdev():
    result = run_bench("noop", lambda: None, iterations=1, warmup=0)
    assert result.stdev_ms == 0.0
