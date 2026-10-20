"""Bounded shared answer cache for stateless Vercel instances, using Upstash REST."""
import hashlib
import json
import time
import httpx
import numpy as np
from phase2_retrieval.cache import CacheHit, normalize
from phase2_retrieval.rate_limit import RedisRateLimiter

STORE = """
local seq = redis.call('INCR', KEYS[3])
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('ZADD', KEYS[2], seq, ARGV[1])
local excess = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[3])
if excess > 0 then
  local old = redis.call('ZRANGE', KEYS[2], 0, excess - 1)
  for _, id in ipairs(old) do
    redis.call('HDEL', KEYS[1], id)
    redis.call('ZREM', KEYS[2], id)
  end
end
for _, key in ipairs(KEYS) do redis.call('EXPIRE', key, ARGV[4]) end
return 1
"""

RECORD = """
redis.call('HINCRBY', KEYS[1], ARGV[1], 1)
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""


class RedisSemanticCache:
    backend = "upstash-redis"

    def __init__(self, settings, namespace, client=None):
        self.client = client or httpx.AsyncClient()
        self.owned_client = client is None
        self.redis = RedisRateLimiter(self.client, settings.redis_url, settings.redis_token, "cache")
        self.prefix = f"symposium:cache:{{{namespace}}}"
        self.threshold, self.ttl, self.max_entries = settings.cache_threshold, settings.cache_ttl, settings.cache_max_entries

    @staticmethod
    def digest(question):
        return hashlib.sha256(normalize(question).encode()).hexdigest()

    async def exact(self, question):
        raw = await self.redis.command(["HGET", self.prefix + ":entries", self.digest(question)])
        if not raw:
            return None
        entry = json.loads(raw)
        if entry["expires"] > time.time():
            return CacheHit(entry["answer"], entry["sources"], 1.0)
        return None

    async def check(self, question, vector):
        entries = await self.redis.command(["HVALS", self.prefix + ":entries"])
        best = None
        v = np.asarray(vector)
        for raw in entries or []:
            entry = json.loads(raw)
            if entry["expires"] <= time.time():
                continue
            candidate = np.asarray(entry["vector"])
            if candidate.shape != v.shape:
                continue
            norm = np.linalg.norm(v) * np.linalg.norm(candidate)
            similarity = float(np.dot(v, candidate) / norm) if norm else -1
            if similarity >= 1 - self.threshold and (best is None or similarity > best.similarity):
                best = CacheHit(entry["answer"], entry["sources"], min(1.0, similarity))
        return best

    async def store(self, question, vector, answer, sources, **usage):
        entry = json.dumps({"vector": vector, "answer": answer, "sources": sources, "expires": time.time() + self.ttl})
        await self.redis.command(["EVAL", STORE, 3, self.prefix + ":entries", self.prefix + ":order", self.prefix + ":seq",
            self.digest(question), entry, self.max_entries, self.ttl])

    async def record(self, hit):
        await self.redis.command(["EVAL", RECORD, 1, self.prefix + ":stats", "hits" if hit else "misses", self.ttl])

    async def stats(self):
        values = await self.redis.command(["HMGET", self.prefix + ":stats", "hits", "misses"])
        hits, misses = [int(value or 0) for value in values]
        total = hits + misses
        return {"hits": hits, "misses": misses, "total": total,
            "hit_rate": f"{hits / total:.1%}" if total else "0.0%", "cost_saved_usd": None,
            "cache_backend": self.backend, "cost_note": "No pricing-based savings estimate configured."}

    async def shutdown(self):
        if self.owned_client:
            await self.client.aclose()
