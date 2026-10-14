"""Small real-service verification; writes no credentials and touches only isolated Redis test keys."""
import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from config import ROOT, Settings
from phase2_retrieval.rate_limit import RedisRateLimiter, RateLimitExceeded, RateLimitUnavailable


async def verify(url):
    s = Settings.from_env()
    report = {"checks": {}, "responses": {}}
    async with httpx.AsyncClient(timeout=60) as client:
        health = await client.get(url + "/health")
        health.raise_for_status()
        report["health"] = health.json()
        report["checks"]["configured_model"] = health.json()["model"] == s.model
        for name, question in [
                ("first", "On which calendar date will Innovate 2026 take place?"),
                ("repeat", "On which calendar date will Innovate 2026 take place?"),
                ("paraphrase", "What date is the Innovate 2026 symposium scheduled for?"),
                ("unsupported", "What is the launch code for the Mars space station?")]:
            start = time.perf_counter()
            response = await client.post(url + "/chat", json={"question": question})
            response.raise_for_status()
            report["responses"][name] = {**response.json(), "seconds": round(time.perf_counter() - start, 3)}
        repeat = report["responses"]["repeat"]
        report["checks"]["grounded_date"] = "March 15, 2026" in report["responses"]["first"]["answer"]
        report["checks"]["exact_hit_and_sources"] = repeat["cache_hit"] and bool(repeat["sources"])
        paraphrase = report["responses"]["paraphrase"]
        report["checks"]["semantic_hit_and_sources"] = paraphrase["cache_hit"] and bool(paraphrase["sources"])
        report["checks"]["unsupported_refusal"] = report["responses"]["unsupported"]["answer"] == "I could not find an answer to this question in the symposium document."
        bad = await client.post(url + "/chat", json={"question": " "})
        report["checks"]["blank_rejected"] = bad.status_code == 422
        # Exhaust only the local client bucket with cheap exact-cache requests.
        statuses = []
        for _ in range(s.rate_limit_requests + 1):
            response = await client.post(url + "/chat", json={"question": "On which calendar date will Innovate 2026 take place?"})
            statuses.append(response.status_code)
            if response.status_code == 429:
                report["retry_after"] = int(response.headers["Retry-After"])
                break
        report["rate_limit_statuses"] = statuses
        report["checks"]["http_429_with_retry"] = 429 in statuses and report.get("retry_after", 0) > 0
        report["stats"] = (await client.get(url + "/stats")).json()
        # Unique expiring keys protect existing app/user data. No flush or broad deletion.
        prefix = "symposium:verification:" + uuid.uuid4().hex
        limiter = RedisRateLimiter(client, s.redis_url, s.redis_token, prefix)
        await limiter.ping()
        async def attempt():
            try:
                await limiter.admit("concurrency", "test-client", 3, 5)
                return True
            except RateLimitExceeded:
                return False
        admitted = await asyncio.gather(*(attempt() for _ in range(10)))
        report["concurrent_admitted"] = sum(admitted)
        report["checks"]["atomic_concurrency"] = sum(admitted) == 3
        second = RedisRateLimiter(client, s.redis_url, s.redis_token, prefix)
        try:
            await second.admit("concurrency", "test-client", 3, 5)
            report["checks"]["shared_denial"] = False
        except RateLimitExceeded as error:
            report["checks"]["shared_denial"] = True
            await asyncio.sleep(error.retry_after + 0.1)
        await second.admit("concurrency", "test-client", 3, 5)
        report["checks"]["expiry_restores_admission"] = True
        invalid = RedisRateLimiter(client, s.redis_url, "invalid-verification-token", prefix)
        try:
            await invalid.admit("invalid", "test", 1, 5)
            report["checks"]["invalid_redis_fails_closed"] = False
        except RateLimitUnavailable:
            report["checks"]["invalid_redis_fails_closed"] = True
    report["passed"] = all(report["checks"].values())
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        report = asyncio.run(verify(args.url.rstrip("/")))
    except Exception as error:
        print(f"Live verification stopped: {type(error).__name__}; inspect service health and local quota.")
        sys.exit(1)
    target = ROOT / "evidence" / "live-verification.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "evidence": str(target)}, indent=2))
    sys.exit(0 if report["passed"] else 1)
