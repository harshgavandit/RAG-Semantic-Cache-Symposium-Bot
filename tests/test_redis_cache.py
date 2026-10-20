import asyncio
import json
import httpx
import pytest
from config import Settings
from phase2_retrieval.redis_cache import RedisSemanticCache
from phase2_retrieval.rate_limit import RateLimitUnavailable


def settings():
    return Settings.from_env({"OPENAI_API_KEY":"test", "CACHE_BACKEND":"redis",
        "UPSTASH_REDIS_REST_URL":"https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN":"test"})


def test_shared_cache_keeps_sources_and_rejects_expired_entries():
    entry = {"answer":"March 15", "sources":["Page 1"], "vector":[1,0], "expires":9999999999}
    def handle(request):
        command = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test"
        result = json.dumps(entry) if command[0] == "HGET" else [json.dumps(entry)]
        return httpx.Response(200, json={"result":result})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            cache = RedisSemanticCache(settings(), "scope", client)
            exact = await cache.exact("date?")
            assert exact.answer == "March 15" and exact.sources == ["Page 1"]
            hit = await cache.check("when?", [0.99,0.01])
            assert hit.sources == ["Page 1"] and hit.similarity > 0.99
            assert await cache.check("where?", [0,1]) is None
            entry["expires"] = 0
            assert await cache.exact("date?") is None
            assert await cache.check("when?", [1,0]) is None
    asyncio.run(run())


def test_cache_store_is_bounded_and_uses_configured_ttl():
    commands = []
    def handle(request):
        command = json.loads(request.content)
        commands.append(command)
        return httpx.Response(200, json={"result":1})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            cache = RedisSemanticCache(settings(), "scope", client)
            await cache.store("date?", [1,0], "March 15", ["Page 1"])
    asyncio.run(run())
    command = commands[0]
    assert command[0] == "EVAL" and command[2] == 3
    assert command[-2:] == [256,86400]
    payload = json.loads(command[-3])
    assert payload["answer"] == "March 15" and payload["sources"] == ["Page 1"]


def test_cache_stats_are_shared_not_fabricated_costs():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200,json={"result":["3","1"]}))) as client:
            cache = RedisSemanticCache(settings(), "scope", client)
            stats = await cache.stats()
            assert stats["total"] == 4 and stats["hit_rate"] == "75.0%"
            assert stats["cost_saved_usd"] is None
    asyncio.run(run())


def test_cache_provider_failure_blocks_safely():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(401,json={"error":"private details"}))) as client:
            cache = RedisSemanticCache(settings(), "scope", client)
            with pytest.raises(RateLimitUnavailable) as error:
                await cache.exact("date?")
            assert "private" not in str(error.value)
    asyncio.run(run())
