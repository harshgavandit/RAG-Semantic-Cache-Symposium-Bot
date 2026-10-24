# Local workload and recovery evidence

These commands measure the real `LocalSemanticCache` adapter and exercise the actual `/chat` and `/health` handlers in an isolated ASGI process. They are development evidence from a Windows laptop, not production traffic, a production incident history, a capacity promise or an SLO. The hosted application uses Redis; these SQLite timings do not measure that deployment.

The checked-in measurements are [local-sqlite-2026-10-24.json](local-sqlite-2026-10-24.json) and [http-recovery-2026-10-24.json](http-recovery-2026-10-24.json). Both include UTC timestamps, runtime versions, base Git SHA, a dirty-working-tree indicator and SHA-256 fingerprints of the measured source. The dirty indicator is intentional: these runs captured the proposed connection-lifetime fix before its commit. Source hashes identify the actual measured files more precisely than the base commit alone.

## Run locally

Run from the repository root with Python 3.12 and the project dependencies installed. The direct cache benchmark needs only NumPy (`2.5.4` in the recorded run) beyond the standard library. The ASGI drill imports the backend and therefore needs the project backend dependencies (`requirements.txt`); its imported provider classes are never instantiated or invoked. Tests additionally use `requirements-dev.txt`. No `.env`, API key, Redis account or provider call is required by either command. Both scripts construct only private temporary SQLite state and remove it after the run.

```powershell
python -m benchmarks.cache_workload --operations 1000 --trials 3 --concurrency 1 4 8 --seed 20261024 --output benchmarks/local-sqlite-new.json
python -m benchmarks.http_recovery --output benchmarks/http-recovery-new.json
python -m pytest -q
```

Output files must be new `.json` files in an existing directory. Existing files are never overwritten. Omit `--output` to print JSON. Exit code `0` means every expected outcome and cleanup check passed; a recorded operation error, wrong result or failed recovery probe produces exit code `1`. Invalid inputs produce exit code `2`. An unexpected setup/cleanup failure raises an error and does not create a misleading success report.

The cache runner accepts 100–10,000 operations per trial, divisible by ten, 1–5 trials, unique concurrency values from `1, 2, 4, 8`, and at most 30,000 total measured operations. A default run measures 9,000 operations plus bounded setup/recovery calls. The writer-lock recovery probe deliberately waits for SQLite's unchanged busy timeout, about five seconds. The commands open no network listener and make zero external requests; Windows `asyncio` still needs permission to create its internal local socket pair. In a restricted agent sandbox that blocks the socket pair, run the same command in a normal local terminal or grant the narrowly scoped local execution request.

## Workload and metric boundaries

Each trial creates a new WAL-mode database, stores 32 labelled synthetic entries, then runs a seeded shuffled schedule of 50% exact reads, 20% semantic reads, 20% misses and 10% writes. Vectors have 64 dimensions. Seeded positive basis vectors provide analytically known matches; semantic requests add a small orthogonal perturbation, misses use a disjoint basis direction, and writes use negative basis vectors under separate keys. This verifies cache matching against known labels. It does not measure an embedding model's paraphrase quality or answer quality.

The cache uses distance threshold `0.1`, entry TTL `3600` seconds and a configured maximum of `256` entries. The timed workload keeps no more than 64 resident entries so its seed entries remain stable; a separate recovery probe verifies actual eviction at a maximum of two. Each concurrency setting repeats the same three schedule seeds. Workers run in a closed loop: each starts its next operation after its preceding operation finishes. No artificial provider delay is added.

`perf_counter` measures each actual adapter await, including the adapter's `asyncio.to_thread` scheduling. It excludes initial seeding, workload queue wait, recovery probes and cleanup. The report gives p50/p95/p99/min/max latency for all attempts and for each operation type, using sorted linear interpolation at rank `(n-1)*p/100`. Throughput divides completed attempts by worker wall time; successful throughput is separately reported. Exceptions and label mismatches are separate counts and never counted as successes. Error output includes exception classes only, without exception messages, filesystem paths or credentials. The workload does not call `record()` for each read; the HTTP drill exercises the route's actual accounting behavior, and a separate adapter probe checks explicit hit/miss increments.

For the recorded 2026-10-24 run on Python `3.12.14`, NumPy `2.5.4`, SQLite `3.53.1`, Windows AMD64:

| Concurrent workers | Attempted operations/s across three trials | Mixed-operation p95 across three trials | Errors / label mismatches |
| --- | --- | --- | --- |
| 1 | 613.3–894.4 | 2.393–3.768 ms | 0 / 0 |
| 4 | 947.2–1176.0 | 8.290–14.070 ms | 0 / 0 |
| 8 | 620.7–632.5 | 19.735–21.085 ms | 0 / 0 |

All 9,000 measured operations matched their expected labels. More concurrency did not consistently improve throughput: eight workers had higher tail latency and lower throughput than four in this run. These short trials share a general-purpose laptop with other activity; they do not establish a sustainable server limit or a statistically controlled hardware comparison. The raw report retains every trial instead of selecting a favorable result. No operation count is converted to dollars.

## Failure and recovery runbook

Run `python -m benchmarks.cache_workload` to reproduce the first five scenarios below without touching application data. Run `python -m benchmarks.http_recovery` for the handler scenarios. The ASGI drill bypasses the production lifespan and injects deterministic embeddings/model/retrieval and explicit limiter states. It uses actual handlers, validation and SQLite behavior, but its 429/Redis-unavailable states are simulations, not evidence from a live Redis service.

| Scenario and injection | Observed behavior in the recorded run | Recovery and success check |
| --- | --- | --- |
| Cold cache, warm lookup, reopen same private database | Cold exact lookup misses; stored answer hits; a newly constructed adapter reads the same persisted answer | Reopen succeeds without a new generated answer |
| Three inserts with `max_entries=2` | Oldest entry disappears; newest remains; SQL count is exactly two | Refill an evicted question through the normal miss path when needed |
| Entry TTL `0.1` seconds, real sleep `0.2` seconds | Entry misses after expiry; no clock mocking is used | Store a fresh entry and verify a hit; route drill separately verifies expired entry invokes its injected generation path again |
| Private adapter path temporarily points at an absent directory | Actual SQLite `OperationalError`; handler health and chat return 503; no injected embedding/model calls occur for the failed chat | Restore the private database path; health returns 200 and the existing warm answer is readable |
| Hold `BEGIN IMMEDIATE` in a second real connection | Store raises `OperationalError` after 5.463 seconds in the recorded run | Roll back/release only that private lock, then verify a new store and read succeed |
| Inject client admission denial | `/chat` returns 429, `Retry-After: 7`; zero embedding/model/retrieval calls | Set injected limiter to allow; next cold request returns 200 |
| Inject unavailable client limiter | `/health` and `/chat` return 503; chat stops before all injected provider work, including for a previously warm exact question | Restore limiter; health and warm chat return 200 |
| Inject unavailable provider admission after an exact miss | `/chat` returns 503 after client and provider admission attempts; zero injected embedding/model/retrieval calls | Restore injected admission; retry the same question and verify 200 with one injected embedding call and an existing semantic cache hit |
| Warm exact and warm semantic handler paths | Exact hit adds zero embedding/model/retrieval calls; semantic hit adds one injected embedding call and zero model/retrieval calls | Both preserve `Page 1` sources; these are scenario call counts, not measured provider latency or price savings |

The direct report records six passing probes, including explicit stats accounting. The ASGI report records 18 passing observations with per-step statuses, admission scopes and injected-component counter deltas. All temporary databases were removed in both runs. The ASGI timings are diagnostic drill durations; the injected components cannot support model latency or throughput claims.

## Connection lifetime bug found by the workload

The first Windows workload exposed a real ownership problem: `with sqlite3.connect(...)` commits or rolls back a transaction but does not close its connection. Some handles survived until garbage collection, so removing a trial's temporary database failed with `WinError 32`. This is a local reproducible finding, not a claimed production incident.

The fix nests the existing transaction context inside `contextlib.closing` at all five connection sites. It preserves successful commits, failed-write rollback, the default SQLite timeout, schema, public cache methods and response contracts. A regression test uses real SQLite connection subclasses to verify explicit closure after initialization, lookup, store, record and stats. Another causes serialization to fail after an expiry deletion and verifies both rollback and handle closure. Both tests failed against the old adapter and passed after the fix. The final workload and HTTP drill remove their temporary state without `gc.collect()` or a cleanup bypass.

The workload's offline suite passed **48 tests** plus four unittest subtests before the additional real-Redis runner safety tests were added. Four warnings come from existing Starlette/AnyIO and FAISS SWIG deprecations. To focus on the connection change:

```powershell
python -m pytest tests/test_cache_connections.py tests/test_cache.py tests/test_backend.py tests/test_benchmark_metrics.py -q
```

This evidence does not establish Redis TTL/eviction behavior, atomic rate-limit behavior, paid-provider reliability or hosted failure recovery. Those require separately bounded service-specific runs; this harness deliberately does not read credentials or offer a broad Redis deletion command.

## Separate real Redis admission drill

The [Redis report](results/redis-admission-2026-10-24.json) measures the actual `RedisRateLimiter` against the configured Upstash REST service. It used **15 real Redis commands**, a unique `evidence:admission:<UUID>` namespace and five-second keys. It never read or changed application keys and used no deletion command.

- Exactly **3 of 12 concurrent attempts** were admitted, with counters 1, 2 and 3; nine were denied.
- A second synthetic identity began at counter 1.
- After actual expiry, the original identity was admitted again at counter 1.
- A separately **injected** HTTP 503 produced `RateLimitUnavailable`; it is not a claimed Upstash outage.

To run another bounded service-specific drill:

```powershell
python -m benchmarks.redis_admission --live --env-file .env --output benchmarks/results/redis-new.json
```

The runner reads only the Redis URL/token, requires explicit `--live`, and allows at most 16 real commands. Redis service latency and actual rate limiting are measured here; answer generation, Redis answer-cache TTL/eviction and hosted HTTP recovery are not. The report records source hashes and individual admission timings without logging the URL, token or request headers.

The [recorded runner snapshot](results/sources/redis_admission-2026-10-24.py.txt) matches the source hash in the report. The current runner additionally rejects invalid or existing output paths before service work and reserves a new file exclusively. This output-safety change required no additional live commands. An interrupted run can leave an explicitly `in_progress` report; it is not a success record.

CI runs the offline regression suite, a 300-operation local workload and the injected HTTP drill. It retains their JSON reports for 30 days and never supplies service credentials or invokes either opt-in live runner. CI timings vary by runner; only operation correctness and recovery outcomes gate the job.
