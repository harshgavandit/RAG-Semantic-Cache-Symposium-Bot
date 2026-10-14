"""Persistent bounded local semantic cache and the optional BetterDB adapter."""
import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
import numpy as np


@dataclass
class CacheHit:
    answer: str
    sources: list[str]
    similarity: float


def normalize(question):
    return " ".join(question.strip().split())


class LocalSemanticCache:
    backend = "local-sqlite"

    def __init__(self, path, namespace, threshold, ttl, max_entries):
        self.path, self.namespace = path, namespace
        self.threshold, self.ttl, self.max_entries = threshold, ttl, max_entries
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS entries (namespace TEXT, question TEXT, vector TEXT, answer TEXT, sources TEXT, expires REAL, created REAL, PRIMARY KEY(namespace,question))")
            db.execute("CREATE TABLE IF NOT EXISTS stats (namespace TEXT PRIMARY KEY, hits INTEGER DEFAULT 0, misses INTEGER DEFAULT 0)")

    async def exact(self, question):
        return await asyncio.to_thread(self._lookup, question, None)

    async def check(self, question, vector):
        return await asyncio.to_thread(self._lookup, question, vector)

    def _lookup(self, question, vector):
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT question,vector,answer,sources FROM entries WHERE namespace=? AND expires>? ORDER BY rowid DESC", (self.namespace, time.time())).fetchall()
        for q, stored, answer, sources in rows:
            if q == normalize(question):
                return CacheHit(answer, json.loads(sources), 1.0)
        if vector is None:
            return None
        v = np.asarray(vector, dtype=float)
        best = None
        for q, stored, answer, sources in rows:
            candidate = np.asarray(json.loads(stored), dtype=float)
            if candidate.shape != v.shape:
                continue
            norm = np.linalg.norm(v) * np.linalg.norm(candidate)
            similarity = float(np.dot(v, candidate) / norm) if norm else -1.0
            if similarity >= 1 - self.threshold and (best is None or similarity > best.similarity):
                best = CacheHit(answer, json.loads(sources), min(1.0, similarity))
        return best

    async def store(self, question, vector, answer, sources, **usage):
        await asyncio.to_thread(self._store, question, vector, answer, sources)

    def _store(self, question, vector, answer, sources):
        now = time.time()
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM entries WHERE expires<=?", (now,))
            db.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?)", (self.namespace, normalize(question), json.dumps(vector), answer, json.dumps(sources), now + self.ttl, time.time_ns()))
            # rowid gives insertion order even when clocks tie or move backwards.
            db.execute("DELETE FROM entries WHERE namespace=? AND question NOT IN (SELECT question FROM entries WHERE namespace=? ORDER BY rowid DESC LIMIT ?)", (self.namespace, self.namespace, self.max_entries))

    async def record(self, hit):
        def write():
            with sqlite3.connect(self.path) as db:
                db.execute("INSERT OR IGNORE INTO stats(namespace) VALUES (?)", (self.namespace,))
                db.execute("UPDATE stats SET hits=hits+?,misses=misses+? WHERE namespace=?", (int(hit), int(not hit), self.namespace))
        await asyncio.to_thread(write)

    async def stats(self):
        def read():
            with sqlite3.connect(self.path) as db:
                return db.execute("SELECT hits,misses FROM stats WHERE namespace=?", (self.namespace,)).fetchone() or (0, 0)
        hits, misses = await asyncio.to_thread(read)
        return {"hits": hits, "misses": misses, "total": hits + misses,
                "hit_rate": f"{hits / (hits + misses):.1%}" if hits + misses else "0.0%",
                "cost_saved_usd": None, "cache_backend": self.backend,
                "cost_note": "Not estimated; no pricing table is configured for the local cache."}

    async def shutdown(self):
        pass


class ValkeySemanticCache:
    backend = "valkey-betterdb"

    def __init__(self, cache, client):
        self.cache, self.client = cache, client

    async def exact(self, question):
        return None

    async def check(self, question, vector):
        result = await self.cache.check(question)
        if not result.hit:
            return None
        payload = json.loads(result.response)
        return CacheHit(payload["answer"], payload["sources"], 1 - (result.similarity or 0))

    async def store(self, question, vector, answer, sources, **usage):
        from betterdb_semantic_cache.types import CacheStoreOptions
        await self.cache.store(question, json.dumps({"answer": answer, "sources": sources}),
                               CacheStoreOptions(**usage))

    async def record(self, hit):
        pass  # BetterDB records lookups itself.

    async def stats(self):
        s = await self.cache.stats()
        return {"hits": s.hits, "misses": s.misses, "total": s.total,
                "hit_rate": f"{s.hit_rate:.1%}", "cost_saved_usd": f"${s.cost_saved_micros / 1_000_000:.4f}",
                "cache_backend": self.backend}

    async def shutdown(self):
        try:
            await self.cache.shutdown()
        finally:
            await self.client.aclose()


async def create_cache(settings, namespace, embeddings, root):
    if settings.cache_backend in {"auto", "valkey"}:
        import valkey.asyncio as valkey
        from betterdb_semantic_cache import SemanticCache, SemanticCacheOptions
        from betterdb_semantic_cache.types import AnalyticsOptions, DiscoveryOptions, ConfigRefreshOptions
        client = valkey.Valkey(host=settings.valkey_host, port=settings.valkey_port,
            password=settings.valkey_password or None, ssl=settings.valkey_tls,
            socket_connect_timeout=2, socket_timeout=2)
        cache = None
        try:
            await client.ping()
            cache = SemanticCache(SemanticCacheOptions(client=client,
                embed_fn=embeddings.aembed_query, name=f"symposium_{namespace}",
                default_threshold=settings.cache_threshold, default_ttl=settings.cache_ttl,
                analytics=AnalyticsOptions(disabled=True), discovery=DiscoveryOptions(enabled=False),
                config_refresh=ConfigRefreshOptions(enabled=False)))
            await cache.initialize()
            return ValkeySemanticCache(cache, client)
        except Exception:
            if cache is not None:
                await cache.shutdown()
            await client.aclose()
            if settings.cache_backend == "valkey":
                raise RuntimeError("Valkey cache unavailable. Start Valkey with its search module or choose CACHE_BACKEND=local.") from None
            print("Valkey unavailable; using persistent local SQLite semantic cache.")
    return LocalSemanticCache(root / "runtime" / "semantic-cache.sqlite3", namespace,
        settings.cache_threshold, settings.cache_ttl, settings.cache_max_entries)
