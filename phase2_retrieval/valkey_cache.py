"""Bounded Valkey cache using the same supplied-vector and Lua contract as Redis."""
from phase2_retrieval.redis_cache import RedisSemanticCache


class ValkeyCommands:
    def __init__(self, client):
        self.client = client

    async def command(self, command):
        return await self.client.execute_command(*command)


class ValkeySemanticCache(RedisSemanticCache):
    backend = "valkey"

    def __init__(self, settings, namespace, client):
        self.client = client
        self.redis = ValkeyCommands(client)
        self.prefix = f"symposium:cache:{{{namespace}}}"
        self.threshold, self.ttl, self.max_entries = (
            settings.cache_threshold, settings.cache_ttl, settings.cache_max_entries)

    async def shutdown(self):
        await self.client.aclose()
