import asyncio
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient


def make_client(tmp_path, monkeypatch, *, limiter_error=None, answer="March 15, 2026. [Page 1]", finish="stop"):
    from config import Settings
    from phase2_retrieval import backend
    from phase2_retrieval.cache import LocalSemanticCache
    counters = {"embeddings": 0, "llm": 0}
    class Embeddings:
        async def aembed_query(self, question):
            counters["embeddings"] += 1
            return [1.0, 0.0]
    class LLM:
        async def ainvoke(self, messages):
            counters["llm"] += 1
            return SimpleNamespace(content=answer, usage_metadata={}, response_metadata={"finish_reason": finish})
    class Index:
        index = SimpleNamespace(ntotal=1, d=2)
        def similarity_search_by_vector(self, vector, k):
            return [SimpleNamespace(page_content="Event date: March 15, 2026", metadata={"page": 0})]
    class Limiter:
        async def admit(self, *args):
            if limiter_error:
                raise limiter_error
        async def ping(self):
            pass
    async def cache_factory(*args):
        return LocalSemanticCache(tmp_path / "cache.db", "test", 0.1, 60, 20)
    monkeypatch.setattr(backend, "load_resources", lambda settings: (Embeddings(), Index(), LLM(), "test"))
    monkeypatch.setattr(backend, "create_cache", cache_factory)
    monkeypatch.setattr(backend, "RedisRateLimiter", lambda *args: Limiter())
    s = Settings.from_env({"OPENAI_API_KEY": "test", "UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "test"})
    return TestClient(backend.create_app(s)), counters


def test_chat_exact_cache_preserves_sources_without_api_work(tmp_path, monkeypatch):
    client, calls = make_client(tmp_path, monkeypatch)
    with client:
        first = client.post("/chat", json={"question": "Date?"})
        second = client.post("/chat", json={"question": "Date?"})
        assert first.status_code == second.status_code == 200
        assert first.json()["cache_hit"] is False
        assert second.json()["cache_hit"] is True
        assert first.json()["sources"] == second.json()["sources"] == ["Page 1"]
        assert calls == {"embeddings": 1, "llm": 1}
        assert client.get("/stats").json()["hits"] == 1


@pytest.mark.parametrize("question", ["", "  ", "x" * 4001])
def test_bad_question_is_rejected_before_api_work(tmp_path, monkeypatch, question):
    client, calls = make_client(tmp_path, monkeypatch)
    with client:
        assert client.post("/chat", json={"question": question}).status_code == 422
        assert calls == {"embeddings": 0, "llm": 0}


@pytest.mark.parametrize("kind,expected", [("limit", 429), ("redis", 503)])
def test_limiter_blocks_before_embeddings_and_llm(tmp_path, monkeypatch, kind, expected):
    from phase2_retrieval.rate_limit import RateLimitExceeded, RateLimitUnavailable
    error = RateLimitExceeded(7) if kind == "limit" else RateLimitUnavailable("blocked")
    client, calls = make_client(tmp_path, monkeypatch, limiter_error=error)
    with client:
        response = client.post("/chat", json={"question": "Date?"})
        assert response.status_code == expected
        assert calls == {"embeddings": 0, "llm": 0}
        if kind == "limit":
            assert response.headers["retry-after"] == "7"


@pytest.mark.parametrize("answer,finish", [("", "stop"), ("Partial [Page 1]", "length"),
    ("Made up [Page 99]", "stop"), ("Uncited answer", "stop"),
    ("Filtered [Page 1]", "content_filter"), ("Partial [Page 1]", "tool_calls"),
    ("Partial [Page 1]", None), ("Partial [Page 1]", "unknown")])
def test_invalid_generation_never_enters_cache(tmp_path, monkeypatch, answer, finish):
    client, calls = make_client(tmp_path, monkeypatch, answer=answer, finish=finish)
    with client:
        assert client.post("/chat", json={"question": "Date?"}).status_code == 502
        assert client.post("/chat", json={"question": "Date?"}).status_code == 502
        assert calls["llm"] == 2


def test_refusal_not_cached_or_cited(tmp_path, monkeypatch):
    from phase2_retrieval.backend import NO_ANSWER
    client, calls = make_client(tmp_path, monkeypatch, answer=NO_ANSWER)
    with client:
        for _ in range(2):
            response = client.post("/chat", json={"question": "Unsupported?"})
            assert response.status_code == 200
            assert response.json()["sources"] == []
            assert response.json()["cache_hit"] is False
        assert calls["llm"] == 2
