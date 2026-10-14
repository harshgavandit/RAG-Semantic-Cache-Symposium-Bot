import asyncio


def test_cache_paraphrase_keeps_sources_and_persists(tmp_path):
    from phase2_retrieval.cache import LocalSemanticCache
    path = tmp_path / "cache.sqlite3"
    cache = LocalSemanticCache(path, "corpus-a", threshold=0.1, ttl=60, max_entries=2)
    asyncio.run(cache.store("When is the event?", [1.0, 0.0], "March 10", ["Page 2"]))
    second = LocalSemanticCache(path, "corpus-a", threshold=0.1, ttl=60, max_entries=2)
    hit = asyncio.run(second.check("Event date?", [0.99, 0.01]))
    assert hit.answer == "March 10"
    assert hit.sources == ["Page 2"]
    assert hit.similarity > 0.99
    assert asyncio.run(second.check("Fees?", [0.0, 1.0])) is None
    different = LocalSemanticCache(path, "corpus-b", threshold=0.1, ttl=60, max_entries=2)
    assert asyncio.run(different.check("When is the event?", [1.0, 0.0])) is None


def test_cache_exact_hit_skips_embedding_and_expires(tmp_path, monkeypatch):
    from phase2_retrieval.cache import LocalSemanticCache
    import phase2_retrieval.cache as module
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    cache = LocalSemanticCache(tmp_path / "cache.db", "scope", threshold=0.1, ttl=5, max_entries=2)
    asyncio.run(cache.store(" Date? ", [1.0, 0.0], "Today", ["Page 1"]))
    assert asyncio.run(cache.exact("Date?")).answer == "Today"
    now[0] += 6
    assert asyncio.run(cache.exact("Date?")) is None


def test_cache_bounded_storage(tmp_path, monkeypatch):
    from phase2_retrieval.cache import LocalSemanticCache
    import phase2_retrieval.cache as module
    # Rapid inserts can tie after SQLite converts nanoseconds to floating point.
    monkeypatch.setattr(module.time, "time_ns", lambda: 123456789)
    cache = LocalSemanticCache(tmp_path / "cache.db", "scope", threshold=0.1, ttl=60, max_entries=2)
    for i in range(3):
        asyncio.run(cache.store(str(i), [1.0, 0.0], str(i), ["Page 1"]))
    assert asyncio.run(cache.exact("0")) is None
    assert asyncio.run(cache.exact("2")).answer == "2"
