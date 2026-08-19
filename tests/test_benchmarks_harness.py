from benchmarks.harness import compare_to_previous, percentile, run_bench, summarize


def test_run_bench_computes_summary_stats():
    calls = []
    result = run_bench("noop", lambda: calls.append(1), iterations=5, warmup=2)
    assert len(calls) == 8  # 1 cold + 2 warmup + 5 timed (issue #58)
    assert result.iterations == 5
    assert result.min_ms <= result.median_ms <= result.max_ms
    assert result.mean_ms >= 0
    assert result.cold_ms >= 0
    assert result.p95_ms >= result.median_ms
    assert result.p99_ms >= result.p95_ms


def test_run_bench_single_iteration_has_zero_stdev():
    result = run_bench("noop", lambda: None, iterations=1, warmup=0)
    assert result.stdev_ms == 0.0


def test_percentile_matches_known_values():
    samples = sorted([10.0, 20.0, 30.0, 40.0, 50.0])
    assert percentile(samples, 0.0) == 10.0
    assert percentile(samples, 1.0) == 50.0


def test_summarize_treats_first_sample_semantics_explicitly():
    result = summarize("op", samples_ms=[5.0, 10.0, 15.0], cold_ms=100.0)
    assert result.cold_ms == 100.0
    assert result.iterations == 3
    assert result.median_ms == 10.0


def test_compare_to_previous_returns_none_with_no_history(tmp_path, monkeypatch):
    import benchmarks.harness as harness

    monkeypatch.setattr(harness, "HISTORY_FILE", tmp_path / "history.jsonl")
    assert compare_to_previous("nonexistent.op") is None


def test_compare_to_previous_computes_delta(tmp_path, monkeypatch):
    import benchmarks.harness as harness

    history_file = tmp_path / "history.jsonl"
    monkeypatch.setattr(harness, "HISTORY_FILE", history_file)

    harness.save_results([run_bench("op", lambda: None, iterations=2, warmup=0)], "t1")
    # Force a known median for the comparison by writing a synthetic second run.
    import json
    with open(history_file, "a") as f:
        f.write(json.dumps({
            "timestamp": "t2",
            "results": [{"name": "op", "median_ms": 999.0, "iterations": 2, "cold_ms": 0,
                         "mean_ms": 999.0, "min_ms": 999.0, "max_ms": 999.0, "p95_ms": 999.0,
                         "p99_ms": 999.0, "stdev_ms": 0.0, "mem_delta_kb": 0.0, "cpu_ms": 0.0}],
        }) + "\n")

    comparison = compare_to_previous("op")
    assert comparison is not None
    assert comparison["latest_median_ms"] == 999.0
    assert comparison["delta_ms"] == 999.0 - comparison["previous_median_ms"]
