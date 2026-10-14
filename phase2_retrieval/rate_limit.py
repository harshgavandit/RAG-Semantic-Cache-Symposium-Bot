"""Atomic, shared fixed-window admission through the configured Upstash REST API."""
import hashlib
import math
import httpx

LUA = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then
  redis.call('SET', KEYS[1], '0', 'PX', ARGV[2])
  count = 0
  ttl = tonumber(ARGV[2])
end
if count >= tonumber(ARGV[1]) then return {0, count, ttl} end
count = redis.call('INCR', KEYS[1])
return {1, count, redis.call('PTTL', KEYS[1])}
"""


class RateLimitExceeded(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__("Rate limit reached. Please try again later.")


class RateLimitUnavailable(Exception):
    pass


class RedisRateLimiter:
    def __init__(self, client, url, token, prefix):
        self.client, self.url, self.token, self.prefix = client, url, token, prefix

    async def command(self, command):
        try:
            response = await self.client.post(self.url, json=command,
                headers={"Authorization": f"Bearer {self.token}"}, timeout=5)
            response.raise_for_status()
            body = response.json()
            if "error" in body or "result" not in body:
                raise ValueError
            return body["result"]
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise RateLimitUnavailable("Redis rate limiting is unavailable; request blocked.") from None

    async def ping(self):
        if await self.command(["PING"]) != "PONG":
            raise RateLimitUnavailable("Redis health check failed")

    async def admit(self, scope, identity, limit, window):
        digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
        result = await self.command(["EVAL", LUA, 1, f"{self.prefix}:{scope}:{digest}", limit, window * 1000])
        if (not isinstance(result, list) or len(result) != 3
                or any(type(value) is not int for value in result)
                or result[0] not in {0, 1} or result[1] < 0 or result[2] <= 0):
            raise RateLimitUnavailable("Redis returned an invalid rate-limit result")
        if result[0] == 0:
            raise RateLimitExceeded(max(1, math.ceil(result[2] / 1000)))
        return result
