"""Measure the real SQLite adapter with labelled synthetic vectors; no providers."""
import argparse
import asyncio
from contextlib import closing
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import sqlite3
import subprocess
import tempfile
import time


@dataclass(frozen=True)
class Sample:
    operation: str
    duration_ms: float
    status: str
    error_type: str = ""


def percentile(values, percent):
    """Linear interpolation on sorted observations, rank=(n-1)*p/100."""
    if not values or not 0 <= percent <= 100:
        raise ValueError("percentile requires observations and percent in [0, 100]")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percent / 100
    lower, upper = math.floor(rank), math.ceil(rank)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def summarize(samples, elapsed_seconds):
    if not samples or elapsed_seconds <= 0:
        raise ValueError("summary requires observations and positive elapsed time")
    def group(items):
        durations = [item.duration_ms for item in items]
        return {
            "attempted": len(items),
            "successful": sum(item.status == "ok" for item in items),
            "mismatches": sum(item.status == "mismatch" for item in items),
            "errors": sum(item.status == "error" for item in items),
            "error_types": dict(Counter(item.error_type for item in items if item.status == "error")),
            "latency_ms": {"min": min(durations), "p50": percentile(durations, 50),
                           "p95": percentile(durations, 95), "p99": percentile(durations, 99),
                           "max": max(durations)},
        }
    report = group(samples)
    report.update({"elapsed_seconds": elapsed_seconds,
                   "attempted_per_second": len(samples) / elapsed_seconds,
                   "successful_per_second": report["successful"] / elapsed_seconds,
                   "by_operation": {operation: group([s for s in samples if s.operation == operation])
                                    for operation in sorted({s.operation for s in samples})}})
    return report


async def timed_call(operation, call, expected):
    start = time.perf_counter()
    try:
        result = await call()
        status = "ok" if expected(result) else "mismatch"
        return Sample(operation, (time.perf_counter() - start) * 1000, status)
    except Exception as error:
        # Exception messages can contain paths/credentials; publish class only.
        return Sample(operation, (time.perf_counter() - start) * 1000, "error", type(error).__name__)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations", type=int, default=1000, help="per trial, 100..10000, divisible by 10")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8])
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--output", type=Path, help="new .json file; existing files are never overwritten")
    args = parser.parse_args(argv)
    if not 100 <= args.operations <= 10000 or args.operations % 10:
        parser.error("operations must be 100..10000 and divisible by 10")
    if not 1 <= args.trials <= 5:
        parser.error("trials must be 1..5")
    if len(set(args.concurrency)) != len(args.concurrency) or not args.concurrency or any(c not in (1, 2, 4, 8) for c in args.concurrency):
        parser.error("concurrency must contain unique values chosen from 1, 2, 4, 8")
    if args.operations * args.trials * len(args.concurrency) > 30000:
        parser.error("total measured operations must not exceed 30000")
    if args.output and (args.output.suffix != ".json" or args.output.exists() or not args.output.parent.is_dir()):
        parser.error("output must be a new .json file in an existing directory")
    return args


DIMENSIONS = 64
SEED_ENTRIES = 32
MAX_ENTRIES = 256
TTL_SECONDS = 3600
THRESHOLD = 0.1


def vector(index, perturbation=0):
    values = [0.0] * DIMENSIONS
    values[index] = 1.0
    values[DIMENSIONS - 2] = perturbation
    return values


async def run_trial(root, operations, concurrency, seed, trial):
    from phase2_retrieval.cache import LocalSemanticCache
    cache = LocalSemanticCache(root / "cache.sqlite3", "synthetic-benchmark", THRESHOLD, TTL_SECONDS, MAX_ENTRIES)
    for index in range(SEED_ENTRIES):
        await cache.store(f"seed-{index}", vector(index), f"answer-{index}", [f"synthetic-page-{index}"])
    # Keep seed entries resident throughout: writes replace one of 32 separate
    # questions, so the timed workload has <=64 entries. Eviction has its own probe.
    labels = ["exact"] * (operations // 2) + ["semantic"] * (operations // 5)
    labels += ["miss"] * (operations // 5) + ["store"] * (operations // 10)
    rng = random.Random(seed)
    rng.shuffle(labels)
    schedule = [(label, rng.randrange(SEED_ENTRIES)) for label in labels]
    samples = []
    cursor = 0

    async def worker():
        nonlocal cursor
        while cursor < len(schedule):
            position = cursor
            cursor += 1  # no await between reservation and increment
            label, index = schedule[position]
            if label == "exact":
                call = lambda: cache.exact(f"seed-{index}")
            elif label == "semantic":
                call = lambda: cache.check(f"paraphrase-{index}", vector(index, 0.05))
            elif label == "miss":
                call = lambda: cache.check("unrelated", vector(DIMENSIONS - 1))
            # Writes use negative vectors, so they cannot steal the semantic hit
            # from the positive seeded basis vectors.
            if label == "store":
                call = lambda: cache.store(f"write-{index}", [-v for v in vector(index)], "synthetic-write", ["synthetic-write"])
            def expected(value):
                if label in {"miss", "store"}:
                    return value is None
                return (value is not None and value.answer == f"answer-{index}"
                        and value.sources == [f"synthetic-page-{index}"]
                        and 0.99 <= value.similarity <= 1.0)
            samples.append(await timed_call(label, call, expected))

    start = time.perf_counter()
    try:
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        elapsed = time.perf_counter() - start
    finally:
        await cache.shutdown()
    result = summarize(samples, elapsed)
    result.update({"trial": trial, "concurrency": concurrency, "seed": seed,
                   "setup_store_calls_excluded": SEED_ENTRIES})
    return result


async def recovery_probes(root):
    """Real adapter and real wall clock; all state belongs to this temp directory."""
    from phase2_retrieval.cache import LocalSemanticCache
    path = root / "recovery.sqlite3"
    cache = LocalSemanticCache(path, "recovery", THRESHOLD, 60, 2)
    probes = []
    def observation(name, passed, **details):
        probes.append({"scenario": name, "passed": bool(passed), **details})
    cold = await cache.exact("first")
    await cache.store("first", vector(0), "first-answer", ["synthetic-page"])
    warm = await cache.exact("first")
    reopened = LocalSemanticCache(path, "recovery", THRESHOLD, 60, 2)
    persisted = await reopened.exact("first")
    observation("cold_warm_reopen", cold is None and warm and persisted
                and warm.answer == persisted.answer == "first-answer",
                cold_miss=cold is None, warm_hit=warm is not None,
                reopened_hit=persisted is not None)
    await cache.store("second", vector(1), "second", [])
    await cache.store("third", vector(2), "third", [])
    oldest, newest = await cache.exact("first"), await cache.exact("third")
    with closing(sqlite3.connect(path)) as db:
        entries = db.execute("SELECT COUNT(*) FROM entries WHERE namespace='recovery'").fetchone()[0]
    observation("bounded_eviction", oldest is None and newest and entries == 2,
                oldest_missing=oldest is None, newest_present=newest is not None,
                resident_entries=entries, max_entries=2)
    ttl_cache = LocalSemanticCache(root / "ttl.sqlite3", "ttl", THRESHOLD, 0.1, 2)
    await ttl_cache.store("short", vector(0), "short", [])
    before = await ttl_cache.exact("short")
    start = time.perf_counter()
    await asyncio.sleep(0.2)
    after = await ttl_cache.exact("short")
    await ttl_cache.store("short", vector(0), "fresh", [])
    refreshed = await ttl_cache.exact("short")
    observation("real_clock_expiry_and_refill", before and after is None and refreshed
                and refreshed.answer == "fresh", ttl_seconds=0.1,
                waited_seconds=time.perf_counter() - start,
                expired_miss=after is None, refill_hit=refreshed is not None)
    # Opening an unreachable database is a real filesystem dependency failure.
    # Point only this private adapter at an absent directory; no live data touched.
    cache.path = root / "absent-directory" / "unavailable.sqlite3"
    failure = await timed_call("unavailable_read", lambda: cache.exact("third"), lambda value: False)
    cache.path = path
    restored = await cache.exact("third")
    observation("database_unavailable_and_restore", failure.status == "error"
                and failure.error_type == "OperationalError" and restored and restored.answer == "third",
                failure_type=failure.error_type, failure_latency_ms=failure.duration_ms,
                restored_hit=restored is not None,
                injection="Private adapter path temporarily points to an absent parent directory")
    # The adapter's normal SQLite busy timeout is retained (5 seconds). This
    # verifies a real writer outage, without monkeypatching the driver or clock.
    with closing(sqlite3.connect(path)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        blocked = await timed_call("locked_write", lambda: cache.store("locked", vector(3), "locked", []), lambda value: False)
        lock.rollback()
    await cache.store("recovered", vector(3), "recovered", [])
    restored = await cache.exact("recovered")
    observation("writer_lock_and_release", blocked.status == "error"
                and blocked.error_type == "OperationalError" and restored and restored.answer == "recovered",
                failure_type=blocked.error_type, failure_latency_ms=blocked.duration_ms,
                recovered_write_and_read=restored is not None,
                injection="BEGIN IMMEDIATE held on a second connection until adapter timeout")
    await cache.record(True)
    await cache.record(False)
    stats = await cache.stats()
    observation("explicit_stats_accounting", stats["hits"] == 1 and stats["misses"] == 1,
                hits=stats["hits"], misses=stats["misses"], total=stats["total"])
    await cache.shutdown()
    await reopened.shutdown()
    await ttl_cache.shutdown()
    return probes


def source_identity(root):
    def git(*args):
        try:
            return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    paths = ["phase2_retrieval/cache.py", "benchmarks/cache_workload.py"]
    return {"head_sha": git("rev-parse", "HEAD"),
            "working_tree_dirty": bool(git("status", "--porcelain")),
            "sha256": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}}


async def run(args):
    import numpy
    root = Path(__file__).resolve().parents[1]
    report = {
        "schema_version": 1,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Real local SQLite adapter timings on labelled seeded synthetic vectors; no embedding, retrieval, model, HTTP or hosted Redis calls.",
        "runtime": {"python": platform.python_version(), "numpy": numpy.__version__,
                    "sqlite": sqlite3.sqlite_version, "platform": platform.system(),
                    "machine": platform.machine(), "processor": platform.processor()},
        "source": source_identity(root),
        "settings": {"operations_per_trial": args.operations, "trials_per_concurrency": args.trials,
                     "concurrency": args.concurrency, "seed": args.seed, "vector_dimensions": DIMENSIONS,
                     "seed_entries": SEED_ENTRIES, "max_entries": MAX_ENTRIES,
                     "ttl_seconds": TTL_SECONDS, "distance_threshold": THRESHOLD,
                     "operation_mix": {"exact": 0.5, "semantic": 0.2, "miss": 0.2, "store": 0.1}},
        "measurement": {"clock": "perf_counter", "percentiles": "sorted linear interpolation, rank=(n-1)*p/100",
                        "latency_scope": "adapter await including its thread scheduling; excludes workload queue wait and setup",
                        "throughput_scope": "completed attempts divided by worker wall time, includes failures",
                        "state": "new temporary SQLite database for each trial; removed after run",
                        "provider_requests": 0, "usd_estimate": None,
                        "limitations": "Closed-loop local workload; synthetic vectors are not evidence of embedding/model quality, production traffic, availability, SLO or hosted Redis performance."},
        "trials": [],
    }
    with tempfile.TemporaryDirectory(prefix="symposium-benchmark-") as temporary:
        directory = Path(temporary)
        for concurrency in args.concurrency:
            for trial in range(1, args.trials + 1):
                # Same schedule seed across concurrency levels supports comparison.
                trial_root = directory / f"c{concurrency}-t{trial}"
                trial_root.mkdir()
                report["trials"].append(await run_trial(trial_root, args.operations, concurrency, args.seed + trial - 1, trial))
        report["recovery_probes"] = await recovery_probes(directory)
    report["temporary_state_removed"] = not directory.exists()
    report["passed"] = (all(t["errors"] == 0 and t["mismatches"] == 0 for t in report["trials"])
                        and all(p["passed"] for p in report["recovery_probes"]) and report["temporary_state_removed"])
    return report


def main():
    args = parse_args()
    report = asyncio.run(run(args))
    output = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        # Exclusive create prevents accidental destruction of previous evidence.
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(output)
        print(f"Wrote {args.output.name}; passed={report['passed']}")
    else:
        print(output, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
