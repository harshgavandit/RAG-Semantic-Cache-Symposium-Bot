"""Real SQLite connections must close on successful and failed adapter work."""
import asyncio
from contextlib import closing
import sqlite3
import pytest


def tracking_connections(monkeypatch):
    import phase2_retrieval.cache as module
    original = sqlite3.connect
    connections = []
    class TrackedConnection(sqlite3.Connection):
        closed = False
        def close(self):
            self.closed = True
            super().close()
    def connect(*args, **kwargs):
        connection = original(*args, factory=TrackedConnection, **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(module.sqlite3, "connect", connect)
    return connections, original


def test_each_cache_operation_explicitly_closes_its_real_connection(tmp_path, monkeypatch):
    from phase2_retrieval.cache import LocalSemanticCache
    connections, _ = tracking_connections(monkeypatch)
    cache = LocalSemanticCache(tmp_path / "cache.sqlite3", "lifetime", 0.1, 60, 2)
    assert connections[-1].closed, "initialization leaves an open SQLite handle"
    async def run():
        assert await cache.exact("missing") is None
        assert connections[-1].closed
        await cache.store("question", [1.0, 0.0], "answer", ["Page 1"])
        assert connections[-1].closed
        assert (await cache.check("paraphrase", [0.99, 0.01])).answer == "answer"
        assert connections[-1].closed
        await cache.record(True)
        assert connections[-1].closed
        assert (await cache.stats())["hits"] == 1
        assert connections[-1].closed
    asyncio.run(run())
    assert len(connections) == 6
    assert all(connection.closed for connection in connections)


def test_failed_store_rolls_back_and_closes_connection(tmp_path, monkeypatch):
    from phase2_retrieval.cache import LocalSemanticCache
    connections, original = tracking_connections(monkeypatch)
    path = tmp_path / "cache.sqlite3"
    cache = LocalSemanticCache(path, "lifetime", 0.1, 60, 2)
    with closing(original(path)) as db, db:
        db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?)", ("lifetime", "expired", "[1,0]", "old", "[]", 0, 0))
    # _store deletes expired entries before serializing this invalid vector.
    # The TypeError must roll back that deletion and still close its handle.
    with pytest.raises(TypeError):
        asyncio.run(cache.store("invalid", object(), "answer", []))
    assert connections[-1].closed
    with closing(original(path)) as db:
        assert db.execute("SELECT question FROM entries").fetchall() == [("expired",)]
