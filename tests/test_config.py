import pytest


def test_config_uses_supplied_model_and_limits():
    from config import Settings
    s = Settings.from_env({"OPENAI_API_KEY": "test", "LLM_MODEL": "custom-model",
                           "RATE_LIMIT_ENABLED": "true", "RATE_LIMIT_REQUESTS": "3",
                           "UPSTASH_REDIS_REST_URL": "https://example.upstash.io",
                           "UPSTASH_REDIS_REST_TOKEN": "test"})
    assert s.model == "custom-model"
    assert s.rate_limit_requests == 3


@pytest.mark.parametrize("field,value", [("RATE_LIMIT_REQUESTS", "0"),
    ("RATE_LIMIT_ENABLED", "tru"), ("CACHE_THRESHOLD", "2"),
    ("LLM_TIMEOUT_SECONDS", "0"), ("LLM_RATE_LIMIT_REQUESTS", "-1")])
def test_config_rejects_invalid_limits(field, value):
    from config import Settings
    with pytest.raises(ValueError, match=field):
        Settings.from_env({"OPENAI_API_KEY": "test", "RATE_LIMIT_ENABLED": "false", field: value})


def test_config_requires_redis_when_limits_enabled():
    from config import Settings
    with pytest.raises(ValueError, match="UPSTASH"):
        Settings.from_env({"OPENAI_API_KEY": "test"})


def test_default_cache_recognizes_documented_paraphrase(tmp_path):
    import asyncio
    from config import Settings
    from phase2_retrieval.cache import LocalSemanticCache
    s = Settings.from_env({"OPENAI_API_KEY": "test", "RATE_LIMIT_ENABLED": "false"})
    cache = LocalSemanticCache(tmp_path / "cache.db", "test", s.cache_threshold, 60, 10)
    asyncio.run(cache.store("Event date?", [1.0, 0.0], "March 15", ["Page 1"]))
    assert asyncio.run(cache.check("When is the event?", [0.875, 0.484123])).answer == "March 15"
    assert asyncio.run(cache.check("Who is the organizer?", [0.5, 0.866025])) is None
