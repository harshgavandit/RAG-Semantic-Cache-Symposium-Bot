"""Guard against misleading percentile, throughput and error summaries."""
import asyncio
from contextlib import redirect_stderr
import io
import unittest


class BenchmarkMetricsTests(unittest.TestCase):
    def test_percentiles_interpolate_and_keep_tail(self):
        from benchmarks.cache_workload import percentile
        self.assertEqual(percentile([40, 10, 30, 20], 50), 25)
        self.assertAlmostEqual(percentile([10, 20, 30, 40], 95), 38.5)
        self.assertEqual(percentile([7], 99), 7)
        with self.assertRaises(ValueError):
            percentile([], 50)
        with self.assertRaises(ValueError):
            percentile([1], 101)

    def test_errors_and_wrong_answers_are_not_counted_as_success(self):
        from benchmarks.cache_workload import Sample, summarize
        result = summarize([
            Sample("exact", 1.0, "ok"),
            Sample("exact", 3.0, "mismatch"),
            Sample("miss", 5.0, "error", "OperationalError"),
        ], elapsed_seconds=2.0)
        self.assertEqual(result["attempted"], 3)
        self.assertEqual(result["successful"], 1)
        self.assertEqual(result["mismatches"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["attempted_per_second"], 1.5)
        self.assertEqual(result["successful_per_second"], 0.5)
        self.assertEqual(result["error_types"], {"OperationalError": 1})
        self.assertEqual(result["latency_ms"]["p50"], 3)
        self.assertEqual(result["by_operation"]["miss"]["errors"], 1)
        with self.assertRaises(ValueError):
            summarize([], elapsed_seconds=0)

    def test_adapter_error_is_recorded_without_private_exception_message(self):
        from benchmarks.cache_workload import timed_call
        async def fail():
            raise OSError("secret-token-and-private-path")
        sample = asyncio.run(timed_call("exact", fail, lambda value: True))
        self.assertEqual(sample.status, "error")
        self.assertEqual(sample.error_type, "OSError")
        self.assertNotIn("secret", repr(sample))
        self.assertGreaterEqual(sample.duration_ms, 0)

    def test_input_limits_reject_unbounded_work_before_running(self):
        from benchmarks.cache_workload import parse_args
        for values in (["--operations", "0"], ["--concurrency", "99"],
                       ["--trials", "99"], ["--operations", "10000", "--trials", "5"]):
            with self.subTest(values=values), self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                parse_args(values)


if __name__ == "__main__":
    unittest.main()
