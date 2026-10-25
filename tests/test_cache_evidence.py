"""Offline checks that retained samples, summaries and source snapshots agree."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from pathlib import Path

import pytest

from benchmarks.cache_workload import Sample, summarize
from benchmarks.verify_cache_workload import OPERATIONS, PROBES, SOURCE_PATHS, verify_report


def example_report():
    root = Path(__file__).resolve().parents[1]
    snapshots = {path: (root / path).read_bytes().decode("utf-8") for path in SOURCE_PATHS}
    samples = [Sample(operation, float(index + 1), "ok") for operation, fraction in OPERATIONS.items()
               for index in range(int(100 * fraction))]
    trial = {**summarize(samples, 1.0), "trial": 1, "concurrency": 1, "seed": 42,
             "setup_store_calls_excluded": 32, "samples": [asdict(sample) for sample in samples]}
    return {"schema_version": 2, "started_at_utc": "2026-10-25T00:00:00+00:00",
            "source": {"head_sha": "a" * 40, "working_tree_dirty": True,
                       "snapshots": snapshots, "sha256": {path: hashlib.sha256(text.encode()).hexdigest()
                                                           for path, text in snapshots.items()}},
            "settings": {"operations_per_trial": 100, "trials_per_concurrency": 1, "concurrency": [1],
                         "seed": 42, "vector_dimensions": 64, "seed_entries": 32, "max_entries": 256,
                         "ttl_seconds": 3600, "distance_threshold": 0.1, "operation_mix": dict(OPERATIONS)},
            "measurement": {"provider_requests": 0, "usd_estimate": None}, "trials": [trial],
            "recovery_probes": [{"scenario": scenario, "passed": True} for scenario in sorted(PROBES)],
            "temporary_state_removed": True, "passed": True}


def test_recomputes_all_sample_metrics_without_optional_dependencies():
    verified = verify_report(example_report())
    assert verified["samples_verified"] == 100
    assert verified["source_snapshots_verified"] == 2
    assert verified["provider_requests_made_by_verifier"] == 0


@pytest.mark.parametrize("mutation", [
    lambda r: r["trials"][0]["latency_ms"].update(p95=999),
    lambda r: r["trials"][0].update(attempted_per_second=999),
    lambda r: r["trials"][0]["by_operation"]["semantic"].update(successful=0),
    lambda r: r["trials"][0]["samples"].pop(),
    lambda r: r["trials"][0]["samples"][0].update(duration_ms=float("nan")),
    lambda r: r["trials"][0]["samples"][0].update(duration_ms=-1),
    lambda r: r["trials"][0]["samples"][0].update(status="error", error_type="private message / token"),
    lambda r: r["trials"][0]["samples"][0].update(status="mismatch"),
    lambda r: r["trials"][0]["samples"][0].update(operation="semantic"),
    lambda r: r["source"]["snapshots"].update({"phase2_retrieval/cache.py": "modified"}),
    lambda r: r["source"].update(head_sha="unknown"),
    lambda r: r["settings"].update(concurrency=[1, 1]),
    lambda r: r["settings"].update(operations_per_trial=True),
    lambda r: r["settings"].update(trials_per_concurrency=5),
    lambda r: r["measurement"].update(provider_requests=1),
    lambda r: r["recovery_probes"].pop(),
    lambda r: r.update(temporary_state_removed=False),
    lambda r: r.update(schema_version=1),
])
def test_rejects_corrupted_or_incomplete_evidence(mutation):
    report = example_report()
    mutation(report)
    with pytest.raises(ValueError):
        verify_report(report)


def test_failure_must_remain_a_failure_after_consistent_recomputation():
    report = example_report()
    trial = report["trials"][0]
    trial["samples"][0].update(status="mismatch")
    trial.update(summarize([Sample(**row) for row in trial["samples"]], trial["elapsed_seconds"]))
    report["passed"] = False
    assert verify_report(report)["reported_workload_passed"] is False


def test_preserves_input_report_without_rewriting_aggregates():
    report = example_report()
    before = deepcopy(report)
    verify_report(report)
    assert report == before
