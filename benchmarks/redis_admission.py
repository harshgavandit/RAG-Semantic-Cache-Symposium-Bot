"""Opt-in isolated admission/expiry drill against the real configured Redis service."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phase2_retrieval.rate_limit import RedisRateLimiter, RateLimitExceeded, RateLimitUnavailable


class BoundedLimiter(RedisRateLimiter):
    """Cap real Redis commands; never touches existing application namespaces."""
    command_count = 0

    async def command(self, command):
        if command[0] not in {'PING', 'EVAL'} or self.command_count >= 16:
            raise RuntimeError('Drill command budget exhausted or unexpected command.')
        self.command_count += 1
        return await super().command(command)


async def run_drill(url, token):
    prefix = 'evidence:admission:' + uuid.uuid4().hex
    limit, window, attempts = 3, 5, 12
    report = {
        'schema_version': 1,
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Real Upstash REST admission, concurrent Lua counter and TTL expiry; separate injected HTTP-503 fail-closed probe. No model calls or production user traffic.',
        'source_base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('phase2_retrieval/rate_limit.py', 'benchmarks/redis_admission.py')},
        'namespace_policy': 'Random evidence:admission UUID prefix, five-second keys, no read/write of application namespaces; no FLUSH or broad deletion.',
        'configuration': {'limit': limit, 'window_seconds': window, 'concurrent_attempts': attempts, 'max_commands': 16},
        'observations': [],
    }
    async with httpx.AsyncClient() as client:
        limiter = BoundedLimiter(client, url, token, prefix)
        try:
            await limiter.ping()
            async def attempt():
                start = time.perf_counter()
                try:
                    result = await limiter.admit('drill', 'synthetic-one', limit, window)
                    return {'status': 'allowed', 'counter': result[1], 'ttl_ms': result[2], 'elapsed_ms': round((time.perf_counter()-start)*1000, 3)}
                except RateLimitExceeded as error:
                    return {'status': 'denied', 'retry_after_seconds': error.retry_after, 'elapsed_ms': round((time.perf_counter()-start)*1000, 3)}
                except Exception as error:
                    return {'status': 'error', 'error_type': type(error).__name__}
            rows = await asyncio.gather(*(attempt() for _ in range(attempts)))
            allowed = [row for row in rows if row['status'] == 'allowed']
            denied = [row for row in rows if row['status'] == 'denied']
            report['attempts'] = rows
            report['observations'].append({'name': 'atomic_concurrent_limit', 'passed': len(allowed) == limit and len(denied) == attempts-limit and sorted(row['counter'] for row in allowed) == [1,2,3]})
            other = await limiter.admit('drill', 'synthetic-two', limit, window)
            report['observations'].append({'name': 'identity_isolation', 'passed': other[1] == 1})
            # Wait a full window after all replies, without refreshing any existing key.
            await asyncio.sleep(window + 0.15)
            recovered = await limiter.admit('drill', 'synthetic-one', limit, window)
            report['observations'].append({'name': 'real_ttl_recovery', 'passed': recovered[1] == 1 and recovered[2] > 0, 'counter': recovered[1], 'ttl_ms': recovered[2]})
        except Exception as error:
            report['observations'].append({'name': 'live_dependency', 'passed': False, 'error_type': type(error).__name__})
        report['real_redis_commands'] = limiter.command_count
    # An explicit injected dependency failure is not a claimed service outage.
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as failing:
        simulated = RedisRateLimiter(failing, 'https://unavailable.invalid', 'test-token', 'isolated')
        try:
            await simulated.admit('drill', 'synthetic-one', limit, window)
        except RateLimitUnavailable:
            blocked = True
        else:
            blocked = False
    report['observations'].append({'name': 'injected_503_fails_closed', 'passed': blocked, 'simulated': True})
    report['passed'] = all(row['passed'] for row in report['observations'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error('No commands sent. Pass --live to run at most 16 isolated Redis commands.')
    if args.output.suffix != '.json' or not args.output.parent.is_dir() or args.output.exists():
        parser.error('Output must be a new .json file in an existing directory; no commands sent.')
    if args.env_file:
        from dotenv import dotenv_values
        values = dotenv_values(args.env_file)
        for name in ('UPSTASH_REDIS_REST_URL', 'UPSTASH_REDIS_REST_TOKEN'):
            if values.get(name) and not os.getenv(name):
                os.environ[name] = values[name]
    url, token = os.getenv('UPSTASH_REDIS_REST_URL'), os.getenv('UPSTASH_REDIS_REST_TOKEN')
    if not url or not token:
        parser.error('Redis URL and token required; no commands sent.')
    # Exclusive creation reserves this run's evidence before any service command.
    with args.output.open('x', encoding='utf-8') as output:
        output.write('{"status": "in_progress"}\n')
        output.flush()
        report = asyncio.run(run_drill(url, token))
        output.seek(0)
        output.write(json.dumps(report, indent=2) + '\n')
        output.truncate()
    print(json.dumps({'passed': report['passed'], 'real_redis_commands': report['real_redis_commands'], 'observations': report['observations']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
