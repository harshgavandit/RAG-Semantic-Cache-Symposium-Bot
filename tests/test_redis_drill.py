"""Offline safety checks for the opt-in real-service drill."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from benchmarks.redis_admission import BoundedLimiter, main


def test_requires_live_before_configuration_or_network():
    with patch('benchmarks.redis_admission.run_drill') as network:
        with pytest.raises(SystemExit) as caught:
            main(['--output', 'not-created.json'])
    assert caught.value.code == 2
    network.assert_not_called()


def test_real_commands_are_hard_bounded():
    async def scenario():
        limiter = BoundedLimiter(None, 'https://unused.invalid', 'unused', 'evidence:test')
        with patch('phase2_retrieval.rate_limit.RedisRateLimiter.command', new_callable=AsyncMock) as command:
            with pytest.raises(RuntimeError):
                await limiter.command(['FLUSHALL'])
            command.assert_not_awaited()
            for _ in range(16):
                await limiter.command(['PING'])
            with pytest.raises(RuntimeError):
                await limiter.command(['PING'])
            with pytest.raises(RuntimeError):
                await limiter.command(['FLUSHALL'])
            assert command.await_count == 16
    asyncio.run(scenario())


def test_bad_destinations_stop_before_service_commands(tmp_path):
    existing = tmp_path / 'existing.json'
    existing.write_text('preserve')
    for path in (existing, tmp_path / 'bad.txt', tmp_path / 'missing' / 'new.json'):
        with patch('benchmarks.redis_admission.run_drill') as network:
            with pytest.raises(SystemExit) as caught:
                main(['--live', '--output', str(path)])
            assert caught.value.code == 2
            network.assert_not_called()
    assert existing.read_text() == 'preserve'
