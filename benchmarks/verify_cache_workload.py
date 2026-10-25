"""Recompute schema-2 cache evidence offline; never load configuration or call providers."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

from benchmarks.cache_workload import Sample, summarize


SOURCE_PATHS = {"phase2_retrieval/cache.py", "benchmarks/cache_workload.py"}
OPERATIONS = {"exact": 0.5, "semantic": 0.2, "miss": 0.2, "store": 0.1}
PROBES = {"cold_warm_reopen", "bounded_eviction", "real_clock_expiry_and_refill",
          "database_unavailable_and_restore", "writer_lock_and_release", "explicit_stats_accounting"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def equal(actual, expected, path):
    """Compare every derived field, allowing only numerical round-off."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), f"{path}: field mismatch")
        for key, value in expected.items():
            equal(actual[key], value, f"{path}.{key}")
    elif type(expected) is float:
        require(number(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10),
                f"{path}: derived number mismatch")
    else:
        require(type(actual) is type(expected) and actual == expected, f"{path}: value mismatch")


def verify_report(report):
    require(report.get("schema_version") == 2, "Schema 2 with raw samples is required; historical schema 1 has no raw durations")
    started = datetime.fromisoformat(report["started_at_utc"])
    require(started.tzinfo is not None and started.utcoffset().total_seconds() == 0, "Timestamp must be UTC")
    source = report["source"]
    require(isinstance(source.get("head_sha"), str) and re.fullmatch(r"[a-f0-9]{40}", source["head_sha"]), "Invalid source commit")
    require(type(source.get("working_tree_dirty")) is bool, "Missing source dirty flag")
    require(set(source["sha256"]) == SOURCE_PATHS and set(source["snapshots"]) == SOURCE_PATHS, "Unexpected source inventory")
    for path in SOURCE_PATHS:
        require(isinstance(source["snapshots"][path], str), "Invalid source snapshot")
        require(hashlib.sha256(source["snapshots"][path].encode("utf-8")).hexdigest() == source["sha256"][path],
                f"Source snapshot mismatch: {path}")
    settings = report["settings"]
    operations, trials, concurrency = (settings["operations_per_trial"], settings["trials_per_concurrency"], settings["concurrency"])
    require(type(operations) is int and 100 <= operations <= 10000 and operations % 10 == 0, "Invalid operation count")
    require(type(trials) is int and 1 <= trials <= 5, "Invalid trial count")
    require(isinstance(concurrency, list) and concurrency and all(type(c) is int and c in (1, 2, 4, 8) for c in concurrency)
            and len(set(concurrency)) == len(concurrency), "Invalid concurrency")
    require(operations * trials * len(concurrency) <= 30000, "Workload exceeds bound")
    require(type(settings["seed"]) is int, "Invalid schedule seed")
    equal(settings["operation_mix"], OPERATIONS, "operation_mix")
    for key, expected in {"vector_dimensions": 64, "seed_entries": 32, "max_entries": 256,
                          "ttl_seconds": 3600, "distance_threshold": 0.1}.items():
        equal(settings[key], expected, key)
    require(report["measurement"]["provider_requests"] == 0 and report["measurement"]["usd_estimate"] is None,
            "Local benchmark cannot report paid-provider measurements")
    require(len(report["trials"]) == trials * len(concurrency), "Trial coverage mismatch")
    all_passed = True
    total = 0
    for index, trial in enumerate(report["trials"]):
        require(trial["concurrency"] == concurrency[index // trials] and trial["trial"] == index % trials + 1,
                "Trial order/identity mismatch")
        require(trial["seed"] == settings["seed"] + index % trials and trial["setup_store_calls_excluded"] == 32,
                "Trial setup mismatch")
        raw = trial["samples"]
        require(isinstance(raw, list) and len(raw) == operations, "Raw sample coverage mismatch")
        samples = []
        for row in raw:
            require(isinstance(row, dict) and set(row) == {"operation", "duration_ms", "status", "error_type"}, "Unexpected sample fields")
            require(row["operation"] in OPERATIONS and row["status"] in {"ok", "mismatch", "error"}, "Invalid sample label/status")
            require(number(row["duration_ms"]), "Invalid sample duration")
            require(isinstance(row["error_type"], str), "Invalid error type")
            require(bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", row["error_type"])) if row["status"] == "error" else row["error_type"] == "",
                    "Error evidence must contain only a class name")
            samples.append(Sample(**row))
        require(dict(Counter(s.operation for s in samples)) == {op: int(operations * ratio) for op, ratio in OPERATIONS.items()},
                "Operation mix mismatch")
        require(number(trial["elapsed_seconds"], positive=True), "Invalid worker wall time")
        recomputed = summarize(samples, trial["elapsed_seconds"])
        observed = {key: value for key, value in trial.items()
                    if key not in {"trial", "concurrency", "seed", "setup_store_calls_excluded", "samples"}}
        equal(observed, recomputed, f"trials[{index}]")
        all_passed = all_passed and recomputed["errors"] == 0 and recomputed["mismatches"] == 0
        total += len(samples)
    probes = report["recovery_probes"]
    require(len(probes) == len(PROBES) and {p["scenario"] for p in probes} == PROBES, "Recovery coverage mismatch")
    require(all(type(p["passed"]) is bool for p in probes), "Invalid recovery verdict")
    require(type(report["temporary_state_removed"]) is bool, "Invalid cleanup verdict")
    expected_passed = all_passed and all(p["passed"] for p in probes) and report["temporary_state_removed"]
    equal(report["passed"], expected_passed, "passed")
    return {"schema_version": 2, "trials_verified": len(report["trials"]), "samples_verified": total,
            "source_snapshots_verified": len(SOURCE_PATHS), "reported_workload_passed": expected_passed,
            "provider_requests_made_by_verifier": 0,
            "limits": "Internal consistency only; no independent execution attestation or production capacity claim."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON value")))
        result = verify_report(report)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        parser.exit(1, f"Evidence verification failed: {type(error).__name__}\n")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["reported_workload_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
