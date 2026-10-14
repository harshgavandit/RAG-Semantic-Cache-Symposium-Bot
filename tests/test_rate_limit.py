import asyncio
import httpx
import pytest


def test_rate_limit_denial_has_retry_and_safe_key():
    from phase2_retrieval.rate_limit import RedisRateLimiter, RateLimitExceeded
    commands = []
    def handle(request):
        import json
        commands.append(json.loads(request.content))
        return httpx.Response(200, json={"result": [0, 3, 4200]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            limiter = RedisRateLimiter(client, "https://example.upstash.io", "secret", "test")
            with pytest.raises(RateLimitExceeded) as error:
                await limiter.admit("client", "192.0.2.1", 3, 60)
            assert error.value.retry_after == 5
    asyncio.run(run())
    assert commands[0][0] == "EVAL"
    assert "192.0.2.1" not in commands[0][3]


@pytest.mark.parametrize("status,payload", [(401, {"error": "secret"}),
    (200, {"error": "broken"}), (200, {"result": "bad"})])
def test_redis_errors_fail_closed_without_secrets(status, payload):
    from phase2_retrieval.rate_limit import RedisRateLimiter, RateLimitUnavailable
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=payload))) as client:
            limiter = RedisRateLimiter(client, "https://example.upstash.io", "secret", "test")
            with pytest.raises(RateLimitUnavailable) as error:
                await limiter.admit("client", "test", 3, 60)
            assert "secret" not in str(error.value)
    asyncio.run(run())
