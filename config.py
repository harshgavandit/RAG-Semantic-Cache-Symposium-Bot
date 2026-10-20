"""Validated configuration, anchored to this project rather than the shell directory."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    redis_url: str = field(repr=False)
    redis_token: str = field(repr=False)
    model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    timeout: float = 30
    max_tokens: int = 1024
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 10
    llm_rate_limit_requests: int = 60
    window: int = 60
    rate_limit_prefix: str = "symposium:ratelimit"
    cache_backend: str = "auto"
    cache_threshold: float = 0.15
    cache_ttl: int = 86400
    cache_max_entries: int = 256
    valkey_host: str = "localhost"
    valkey_port: int = 6379
    valkey_password: str = field(default="", repr=False)
    valkey_tls: bool = False
    backend_url: str = "http://127.0.0.1:8000"
    hosted: bool = False
    data_root: Path = ROOT

    @classmethod
    def from_env(cls, values=None):
        if values is None:
            load_dotenv(ROOT / ".env", override=False)
            values = os.environ
        def get(name, default=""):
            return str(values.get(name, default)).strip()
        def positive(name, default, cast=int):
            try:
                value = cast(get(name, default))
                if value <= 0:
                    raise ValueError
                return value
            except (ValueError, TypeError):
                raise ValueError(f"{name} must be a positive number") from None
        def boolean(name, default):
            value = get(name, default).lower()
            if value not in {"true", "false", "1", "0"}:
                raise ValueError(f"{name} must be true or false")
            return value in {"true", "1"}
        enabled = boolean("RATE_LIMIT_ENABLED", "true")
        key = get("OPENAI_API_KEY")
        if not key or key.startswith("sk-your"):
            raise ValueError("OPENAI_API_KEY must be configured")
        url, token = get("UPSTASH_REDIS_REST_URL"), get("UPSTASH_REDIS_REST_TOKEN")
        parsed = urlparse(url)
        if enabled and (parsed.scheme != "https" or not parsed.hostname or not token
                        or parsed.username or parsed.password or parsed.query):
            raise ValueError("UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are required")
        try:
            threshold = float(get("CACHE_THRESHOLD", "0.15"))
            if not 0 <= threshold <= 0.3:
                raise ValueError
        except ValueError:
            raise ValueError("CACHE_THRESHOLD must be between 0 and 0.3") from None
        hosted = get("VERCEL") == "1"
        backend = get("CACHE_BACKEND", "redis" if hosted else "auto").lower()
        if backend not in {"auto", "local", "valkey", "redis"} or (hosted and backend != "redis"):
            raise ValueError("CACHE_BACKEND must be redis on Vercel, or auto/local/valkey/redis locally")
        if backend == "redis" and (parsed.scheme != "https" or not parsed.hostname or not token):
            raise ValueError("UPSTASH credentials are required for Redis caching")
        model = get("LLM_MODEL") or get("OPENAI_MODEL") or "gpt-4o-mini"
        return cls(api_key=key, redis_url=url.rstrip("/"), redis_token=token, model=model,
            embedding_model=get("EMBEDDING_MODEL", "text-embedding-3-small"),
            timeout=positive("LLM_TIMEOUT_SECONDS", 30, float),
            max_tokens=positive("LLM_MAX_COMPLETION_TOKENS", 1024),
            rate_limit_enabled=enabled, rate_limit_requests=positive("RATE_LIMIT_REQUESTS", 10),
            llm_rate_limit_requests=positive("LLM_RATE_LIMIT_REQUESTS", 60),
            window=positive("RATE_LIMIT_WINDOW_SECONDS", 60),
            rate_limit_prefix=get("RATE_LIMIT_PREFIX", "symposium:hosted:ratelimit" if hosted else "symposium:ratelimit"),
            cache_backend=backend, cache_threshold=threshold,
            cache_ttl=positive("CACHE_TTL_SECONDS", 86400),
            cache_max_entries=positive("CACHE_MAX_ENTRIES", 256),
            valkey_host=get("VALKEY_HOST", "localhost"), valkey_port=positive("VALKEY_PORT", 6379),
            valkey_password=get("VALKEY_PASSWORD"), valkey_tls=boolean("VALKEY_TLS", "false"),
            backend_url=get("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/"),
            hosted=hosted, data_root=ROOT / "deploy_data" if hosted else ROOT)
