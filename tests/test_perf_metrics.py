import threading

from utils import perf_metrics


def setup_function():
    perf_metrics.reset()


def test_record_and_snapshot_basic_stats():
    perf_metrics.record("database", "db_connect", 10.0)
    perf_metrics.record("database", "db_connect", 20.0)
    perf_metrics.record("database", "db_connect", 30.0)

    snap = perf_metrics.snapshot()
    stats = snap["database"]["db_connect"]
    assert stats["count"] == 3
    assert stats["last"] == 30.0
    assert stats["mean"] == 20.0
    assert stats["p95"] >= stats["mean"]


def test_snapshot_omits_categories_with_no_samples():
    assert perf_metrics.snapshot() == {}


def test_samples_are_capped_at_max_length():
    for i in range(100):
        perf_metrics.record("sql_editor", "query_execute", float(i))
    stats = perf_metrics.snapshot()["sql_editor"]["query_execute"]
    assert stats["count"] == perf_metrics._MAX_SAMPLES
    assert stats["last"] == 99.0


def test_counter_inc_and_get():
    perf_metrics.counter_inc("schema_cache", "hit")
    perf_metrics.counter_inc("schema_cache", "hit")
    perf_metrics.counter_inc("schema_cache", "miss")

    counts = perf_metrics.counter_get("schema_cache")
    assert counts == {"hit": 2, "miss": 1}


def test_counter_get_unknown_name_returns_empty_dict():
    assert perf_metrics.counter_get("nonexistent") == {}


def test_reset_clears_samples_and_counters():
    perf_metrics.record("database", "db_connect", 5.0)
    perf_metrics.counter_inc("schema_cache", "hit")
    perf_metrics.reset()
    assert perf_metrics.snapshot() == {}
    assert perf_metrics.counter_get("schema_cache") == {}


def test_active_tasks_tracks_started_and_finished():
    assert perf_metrics.active_tasks() == {}

    perf_metrics.task_started("schema_fetch")
    perf_metrics.task_started("schema_fetch")
    perf_metrics.task_started("export")
    assert perf_metrics.active_tasks() == {"schema_fetch": 2, "export": 1}

    perf_metrics.task_finished("schema_fetch")
    assert perf_metrics.active_tasks() == {"schema_fetch": 1, "export": 1}

    perf_metrics.task_finished("schema_fetch")
    perf_metrics.task_finished("export")
    assert perf_metrics.active_tasks() == {}


def test_task_finished_without_started_does_not_go_negative():
    perf_metrics.task_finished("nonexistent")
    assert perf_metrics.active_tasks() == {}


def test_record_is_thread_safe_under_concurrent_writers():
    def writer(n):
        for i in range(50):
            perf_metrics.record("database", "db_connect", float(n * 100 + i))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats = perf_metrics.snapshot()["database"]["db_connect"]
    assert stats["count"] == perf_metrics._MAX_SAMPLES
