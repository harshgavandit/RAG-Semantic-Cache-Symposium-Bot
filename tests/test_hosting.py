from dataclasses import replace
import pytest


def test_hosted_config_selects_shared_cache_and_private_build_data():
    from config import Settings, ROOT
    s = Settings.from_env({"OPENAI_API_KEY":"test", "VERCEL":"1",
        "UPSTASH_REDIS_REST_URL":"https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN":"test"})
    assert s.cache_backend == "redis"
    assert s.data_root == ROOT / "deploy_data"
    assert s.rate_limit_prefix == "symposium:hosted:ratelimit"


def test_hosted_config_does_not_allow_ephemeral_local_cache():
    from config import Settings
    with pytest.raises(ValueError, match="CACHE_BACKEND"):
        Settings.from_env({"OPENAI_API_KEY":"test", "VERCEL":"1", "CACHE_BACKEND":"local",
            "UPSTASH_REDIS_REST_URL":"https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN":"test"})


def test_browser_page_and_assets_load(tmp_path, monkeypatch):
    from tests.test_backend import make_client
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert 'id="chat-form"' in response.text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/styles.css").status_code == 200


def test_serverless_boot_without_promoted_public_directory(tmp_path, monkeypatch):
    from config import Settings
    from phase2_retrieval import backend
    from fastapi.testclient import TestClient
    s = Settings.from_env({"OPENAI_API_KEY":"test", "VERCEL":"1",
        "UPSTASH_REDIS_REST_URL":"https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN":"test"})
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    app = backend.create_app(s)
    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code in {302,307}
    assert response.headers["location"] == "/index.html"
