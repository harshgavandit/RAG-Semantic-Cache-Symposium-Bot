"""Isolated ASGI recovery drill: real handlers/SQLite, explicitly injected providers."""
import argparse
import asyncio
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import sqlite3
import tempfile
import time
from types import SimpleNamespace

from benchmarks.cache_workload import source_identity


async def drill():
    started_at = datetime.now(timezone.utc).isoformat()
    import httpx
    from config import Settings
    from phase2_retrieval.backend import create_app
    from phase2_retrieval.cache import LocalSemanticCache
    from phase2_retrieval.rate_limit import RateLimitExceeded, RateLimitUnavailable

    calls = Counter({"embeddings": 0, "llm": 0, "retrieval": 0})
    admissions = []

    class Embeddings:
        async def aembed_query(self, question):
            calls["embeddings"] += 1
            return [1.0, 0.0] if question in {"Date?", "When?"} else [0.0, 1.0]

    class Model:
        async def ainvoke(self, messages):
            calls["llm"] += 1
            return SimpleNamespace(content="Synthetic symposium date: March 15. [Page 1]",
                                   usage_metadata={}, response_metadata={"finish_reason": "stop"})

    class Store:
        index = SimpleNamespace(ntotal=1, d=2)
        def similarity_search_by_vector(self, vector, k):
            calls["retrieval"] += 1
            return [SimpleNamespace(page_content="Synthetic symposium date: March 15.", metadata={"page": 0})]

    class Limiter:
        mode = "allow"
        async def admit(self, scope, identity, limit, window):
            admissions.append(scope)
            if scope == "client" and self.mode == "client_limit":
                raise RateLimitExceeded(7)
            if (scope == "client" and self.mode == "client_unavailable") or (scope == "provider" and self.mode == "provider_unavailable"):
                raise RateLimitUnavailable("Injected unavailable dependency")
            return [1, 1, 60000]
        async def ping(self):
            if self.mode == "client_unavailable":
                raise RateLimitUnavailable("Injected unavailable dependency")

    settings = Settings(api_key="unused-in-injected-drill", redis_url="", redis_token="", cache_backend="local")
    app = create_app(settings)
    limiter = Limiter()
    # Do not start the production lifespan or instantiate provider clients. The
    # production route functions read these same app.state dependencies.
    app.state.settings = settings
    app.state.embeddings, app.state.llm, app.state.store = Embeddings(), Model(), Store()
    app.state.limiter = limiter
    observations = []
    with tempfile.TemporaryDirectory(prefix="symposium-http-drill-") as temporary:
        directory = Path(temporary)
        database = directory / "cache.sqlite3"
        cache = LocalSemanticCache(database, "http-drill", 0.1, 60, 16)
        app.state.cache = cache
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated-asgi.invalid") as client:
            async def request(name, expected_status, expected_calls, *, question=None, path="/chat", cache_hit=None, retry_after=None, expected_admissions=None):
                before = calls.copy()
                before_admissions = len(admissions)
                start = time.perf_counter()
                response = await (client.post(path, json={"question": question}) if question is not None else client.get(path))
                duration = (time.perf_counter() - start) * 1000
                delta = {key: calls[key] - before[key] for key in ("embeddings", "llm", "retrieval")}
                body = response.json()
                observed_admissions = admissions[before_admissions:]
                passed = response.status_code == expected_status and list(delta.values()) == list(expected_calls)
                if cache_hit is not None:
                    passed = passed and body.get("cache_hit") is cache_hit and body.get("sources") == ["Page 1"]
                if retry_after is not None:
                    passed = passed and response.headers.get("retry-after") == str(retry_after)
                if expected_admissions is not None:
                    passed = passed and observed_admissions == expected_admissions
                observations.append({"scenario": name, "passed": bool(passed), "http_status": response.status_code,
                    "duration_ms": duration, "injected_component_calls": delta, "admission_scopes": observed_admissions,
                    "cache_hit": body.get("cache_hit"), "retry_after": response.headers.get("retry-after")})

            await request("cold_request", 200, (1, 1, 1), question="Date?", cache_hit=False, expected_admissions=["client", "provider"])
            await request("exact_warm_request", 200, (0, 0, 0), question="Date?", cache_hit=True, expected_admissions=["client"])
            await request("semantic_warm_request", 200, (1, 0, 0), question="When?", cache_hit=True, expected_admissions=["client", "provider"])
            limiter.mode = "client_limit"
            await request("client_limit_before_provider_work", 429, (0, 0, 0), question="Different?", retry_after=7, expected_admissions=["client"])
            limiter.mode = "allow"
            await request("client_limit_recovery", 200, (1, 1, 1), question="Different?", cache_hit=False)
            limiter.mode = "client_unavailable"
            await request("limiter_health_unavailable", 503, (0, 0, 0), path="/health")
            await request("limiter_unavailable_before_provider_work", 503, (0, 0, 0), question="Date?", expected_admissions=["client"])
            limiter.mode = "allow"
            await request("limiter_health_recovered", 200, (0, 0, 0), path="/health")
            await request("limiter_recovery_exact_warm", 200, (0, 0, 0), question="Date?", cache_hit=True)
            limiter.mode = "provider_unavailable"
            await request("provider_admission_unavailable_before_embedding", 503, (0, 0, 0), question="Uncached?", expected_admissions=["client", "provider"])
            limiter.mode = "allow"
            await request("provider_admission_recovered_semantic_hit", 200, (1, 0, 0), question="Uncached?", cache_hit=True, expected_admissions=["client", "provider"])
            cache.path = directory / "absent-directory" / "cache.sqlite3"
            await request("cache_health_unavailable", 503, (0, 0, 0), path="/health")
            await request("cache_unavailable_before_provider_work", 503, (0, 0, 0), question="Date?", expected_admissions=["client"])
            cache.path = database
            await request("cache_health_recovered", 200, (0, 0, 0), path="/health")
            await request("cache_recovery_exact_warm", 200, (0, 0, 0), question="Date?", cache_hit=True)
            ttl_cache = LocalSemanticCache(directory / "ttl.sqlite3", "http-ttl", 0.1, 0.1, 16)
            app.state.cache = ttl_cache
            await request("ttl_cold_fill", 200, (1, 1, 1), question="TTL?", cache_hit=False)
            await request("ttl_before_expiry", 200, (0, 0, 0), question="TTL?", cache_hit=True)
            await asyncio.sleep(0.2)
            await request("ttl_expired_refill", 200, (1, 1, 1), question="TTL?", cache_hit=False)
            await ttl_cache.shutdown()
        await cache.shutdown()
    root = Path(__file__).resolve().parents[1]
    source = source_identity(root)
    for path in ("benchmarks/http_recovery.py", "phase2_retrieval/backend.py", "phase2_retrieval/rate_limit.py", "config.py"):
        source["sha256"][path] = hashlib.sha256((root / path).read_bytes()).hexdigest()
    return {"schema_version": 1, "started_at_utc": started_at,
            "runtime": {"python": platform.python_version(), "httpx": httpx.__version__,
                        "sqlite": sqlite3.sqlite_version, "platform": platform.system()},
            "scope": "In-process HTTP/ASGI recovery simulation using actual create_app handlers and SQLite adapter, with injected deterministic embedding/model/retrieval and injected limiter states. No external Redis, AI, HTTP network or paid work.",
            "limitations": "Status and counter evidence proves handler ordering/recovery under injected faults; does not prove real Redis atomicity, provider quality, deployment health or production operation. Production lifespan intentionally bypassed.",
            "source": source, "observations": observations,
            "total_injected_component_calls": dict(calls), "actual_provider_requests": 0,
            "temporary_state_removed": not directory.exists(),
            "passed": all(item["passed"] for item in observations) and not directory.exists()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new .json file in an existing directory")
    args = parser.parse_args()
    if args.output and (args.output.suffix != ".json" or args.output.exists() or not args.output.parent.is_dir()):
        parser.error("output must be a new .json file in an existing directory")
    # Production handlers print sanitized fault classes. Keep stdout JSON-only.
    with redirect_stdout(io.StringIO()):
        report = asyncio.run(drill())
    output = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(output)
        print(f"Wrote {args.output.name}; passed={report['passed']}")
    else:
        print(output, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
