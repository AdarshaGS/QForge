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

- `db_connect.postgresql` — connection establishment, PostgreSQL variant
- `query_execute.postgresql_select_10k` — query execution, 10k-row select variant
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

`bench_db_service.py` and `bench_connection_switching.py` run against a
local PostgreSQL server at `qforge_test@localhost:5432` (see
`benchmarks/_pg_fixture.py` — same convention `tests/test_db_service_postgresql.py`
and `tests/test_query_cost.py` already use). Both check reachability first
and print a one-line skip message instead of crashing when that server
isn't running, so `python -m benchmarks.run` still completes cleanly
without it — just with fewer results. MySQL/SSH-tunnel variants need the
same treatment once a standard local-server convention exists for them
too. (Before sqlite support was removed from the app, these two modules
ran against a throwaway sqlite file instead, purely because it needed no
live server — not for any dialect-specific reason.)

`bench_startup.py` needs no live database at all: `DbService.connect()` is
stubbed to a no-op success (scoped with `mock.patch.object`, so it reverts
before any other module in `benchmarks/run.py`'s `MODULES` list runs), since
this benchmark measures pure window/UI construction time, not connection
behavior.

## Modules

- `bench_db_service.py` — PostgreSQL connection establishment, query execution.
- `bench_startup.py` — application startup stages (issue #59): app init,
  main window construction, connection-manager-ready, UI-interactive. Built
  under `QT_QPA_PLATFORM=offscreen` with a stubbed, pre-accepted connection
  dialog and a stubbed `DbService.connect()` (same pattern as
  `tests/test_main_window_focus_after_connect.py`) so it runs unattended
  with no live DB or real user interaction.
- `bench_connection_switching.py` — connection/database switching and
  `ConnectionPanel` construction time (issue #60), against PostgreSQL for
  the same reason as `bench_db_service.py`.
