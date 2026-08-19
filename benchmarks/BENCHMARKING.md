# QForge Benchmarks

Standalone, offline timing scripts for tracking QForge's performance across
releases (issue #58). Distinct from `utils/perf_metrics.py`, which is the
*live*, in-process registry the running app and its developer overlay
(`ui/perf_overlay.py`, issue #43) read from — these scripts run in isolation
and persist history to disk.

## Running

```
python -m benchmarks.run
```

Runs every module registered in `benchmarks/run.py`'s `MODULES` list, prints
a one-line summary per benchmark, and appends a timestamped record to
`benchmarks/results/history.jsonl`.

A single module can also be run directly, e.g. `python -m benchmarks.bench_db_service`.

## Naming convention

`<component>.<operation>[_<variant>]`, all lowercase, dot-separated:

- `db_connect.sqlite` — connection establishment, sqlite variant
- `query_execute.sqlite_select_10k` — query execution, 10k-row select variant
- `startup.window_created` — one stage of application startup

Keep the component prefix stable across variants of the same operation so
`compare_to_previous()` (see below) is comparing like-for-like.

## What each number means

Every `BenchResult` (`benchmarks/harness.py`) reports:

- **cold** — the first, untimed-in-the-old-harness call, now timed and
  reported separately. This is the call that pays one-time costs a steady
  -state loop never sees again (imports, disk cache misses, first
  connection handshake).
- **median / mean / min / max / stdev / p95 / p99** — computed over
  `iterations` further calls, after `warmup` additional untimed calls. This
  is the "warm" number: steady-state cost once caches/connections are hot.
- **mem_delta_kb** — resident-set-size delta (`resource.getrusage().ru_maxrss`,
  normalized to KB — it's bytes on macOS, KB on Linux) across the timed
  portion of the run. Can be noisy for cheap/fast benchmarks; more
  meaningful for benchmarks that allocate real data (e.g. loading a large
  result set).
- **cpu_ms** — user+system CPU time consumed (`ru_utime + ru_stime` delta),
  distinct from wall-clock `median_ms` — useful for telling "slow because
  waiting on I/O" apart from "slow because computing."

No `psutil` dependency: everything comes from the stdlib `resource` module,
already available everywhere QForge runs.

`mem_delta_kb`/`cpu_ms` are only populated for benchmarks that go through
`run_bench` (it wraps the timed calls in `resource.getrusage`). Benchmarks
built from external samples via `summarize()` directly — `bench_startup.py`,
which reads elapsed times back out of `utils/perf_metrics` instead of timing
a function call itself — report `0` for both; there's no single call for
`getrusage` to bracket. Latency numbers (cold/median/p95/p99) are accurate
either way.

## Baseline comparison

`compare_to_previous(name)` reads `benchmarks/results/history.jsonl` and
diffs the latest run's `median_ms` for `name` against the immediately
preceding run, returning delta in both ms and percent (or `None` if there's
no prior run yet). This is what makes `history.jsonl` a real baseline, not
just an append-only log — regression-checking a benchmark means calling this
after `python -m benchmarks.run` and looking at `delta_pct`.

## Reproducibility — what's controlled, what isn't

- Dataset size and shape are fixed in code (e.g. `bench_db_service.py`'s
  `ROW_COUNT = 10_000`), not sampled from a live environment, so runs on the
  same machine are comparable to each other.
- `iterations`/`warmup` counts are fixed per benchmark call, not
  environment-dependent.
- **Not controlled**: absolute numbers are single-machine, single-process.
  They are **not** comparable across different hardware, OS versions, or
  machine load at the time of the run. Use `compare_to_previous()` on the
  *same* machine to catch regressions; don't compare `history.jsonl` entries
  from two different developers' laptops.

## Scope: which database backends

Only `sqlite` is benchmarked in the default, unattended suite — it needs no
live server, so it's the only backend that can run offline/in CI. MySQL,
PostgreSQL, and SSH-tunnel variants need a reachable server and are
deliberately out of scope here; add an opt-in, separately-invoked module for
those if/when a benchmark server becomes available (same reasoning already
documented in `bench_db_service.py`).

## Modules

- `bench_db_service.py` — sqlite connection establishment, query execution.
- `bench_startup.py` — application startup stages (issue #59): app init,
  main window construction, connection-manager-ready, UI-interactive. Built
  under `QT_QPA_PLATFORM=offscreen` with a stubbed, pre-accepted connection
  dialog (same pattern as `tests/test_main_window_focus_after_connect.py`)
  so it runs unattended with no live DB or real user interaction.
- `bench_connection_switching.py` — connection/database switching and
  `ConnectionPanel` construction time (issue #60), sqlite-only for the same
  reason as `bench_db_service.py`.
