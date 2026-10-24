"""The Valkey adapter reuses supplied vectors and honors the bounded cache contract."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from config import Settings
from phase2_retrieval.cache import create_cache


class MemoryValkey:
    def __init__(self):
        self.entries = {}
        self.order = []
        self.counts = {'hits': 0, 'misses': 0}
        self.commands = []
        self.closed = False

    async def ping(self):
        return True

    async def execute_command(self, *command):
        self.commands.append(command)
        if command[0] == 'HGET':
            return self.entries.get(command[-1])
        if command[0] == 'HVALS':
            return list(self.entries.values())
        if command[0] == 'HMGET':
            return [self.counts['hits'], self.counts['misses']]
        if command[0] == 'EVAL' and command[2] == 3:
            digest, payload, maximum, ttl = command[-4:]
            self.entries[digest] = payload
            self.order = [item for item in self.order if item != digest] + [digest]
            while len(self.order) > maximum:
                self.entries.pop(self.order.pop(0))
            return 1
        if command[0] == 'EVAL' and command[2] == 1:
            self.counts[command[-2]] += 1
            return 1
        raise AssertionError('Unexpected command')

    async def aclose(self):
        self.closed = True


def test_valkey_supplied_vectors_exact_lookup_capacity_ttl_and_sources(monkeypatch, tmp_path):
    import valkey.asyncio
    client = MemoryValkey()
    monkeypatch.setattr(valkey.asyncio, 'Valkey', lambda **kwargs: client)
    async def no_embedding(question):
        pytest.fail('The cache must reuse the vector already embedded by the handler')
    settings = Settings.from_env({'OPENAI_API_KEY': 'test', 'RATE_LIMIT_ENABLED': 'false',
        'CACHE_BACKEND': 'valkey', 'CACHE_MAX_ENTRIES': '2', 'CACHE_TTL_SECONDS': '73'})
    async def run():
        cache = await create_cache(settings, 'fixture', SimpleNamespace(aembed_query=no_embedding), tmp_path)
        await cache.store('first?', [1., 0.], 'First [Page 1]', ['Page 1'])
        assert (await cache.exact(' first? ')).sources == ['Page 1']
        assert (await cache.check('paraphrase?', [.99, .01])).answer == 'First [Page 1]'
        assert await cache.check('unrelated?', [0., 1.]) is None
        await cache.store('second?', [0., 1.], 'Second', ['Page 2'])
        await cache.store('third?', [-1., 0.], 'Third', ['Page 3'])
        assert await cache.exact('first?') is None
        assert (await cache.exact('third?')).sources == ['Page 3']
        await cache.record(True)
        await cache.record(False)
        stats = await cache.stats()
        assert stats['hits'] == stats['misses'] == 1
        assert stats['cost_saved_usd'] is None
        assert stats['cache_backend'] == 'valkey'
        for command in client.commands:
            if command[0] == 'EVAL' and command[2] == 3:
                assert command[-2:] == (2, 73)
                assert json.loads(command[-3])['vector'] in ([1., 0.], [0., 1.], [-1., 0.])
        # Expired entries are ignored on exact and semantic reads.
        for key, value in list(client.entries.items()):
            entry = json.loads(value)
            entry['expires'] = 0
            client.entries[key] = json.dumps(entry)
        assert await cache.exact('third?') is None
        assert await cache.check('third paraphrase?', [-1., 0.]) is None
        await cache.shutdown()
        assert client.closed
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['auto', 'valkey'])
def test_valkey_startup_failure_closes_client_and_respects_explicit_backend(monkeypatch, tmp_path, mode):
    import valkey.asyncio
    client = MemoryValkey()
    async def failed_ping():
        raise ConnectionError('unavailable')
    client.ping = failed_ping
    monkeypatch.setattr(valkey.asyncio, 'Valkey', lambda **kwargs: client)
    settings = Settings.from_env({'OPENAI_API_KEY': 'test', 'RATE_LIMIT_ENABLED': 'false',
        'CACHE_BACKEND': mode})
    async def run():
        if mode == 'valkey':
            with pytest.raises(RuntimeError, match='Valkey cache unavailable'):
                await create_cache(settings, 'fixture', None, tmp_path)
        else:
            cache = await create_cache(settings, 'fixture', None, tmp_path)
            assert cache.backend == 'local-sqlite'
            await cache.store('Date?', [1., 0.], 'Tomorrow', ['Page 1'])
            assert (await cache.exact('Date?')).answer == 'Tomorrow'
            await cache.shutdown()
        assert client.closed
    asyncio.run(run())


def test_valkey_command_bridge_accepts_bytes_from_normal_client(monkeypatch, tmp_path):
    import valkey.asyncio
    client = MemoryValkey()
    original_command = client.execute_command
    async def bytes_command(*command):
        result = await original_command(*command)
        if isinstance(result, str):
            return result.encode()
        if isinstance(result, list):
            return [item.encode() if isinstance(item, str) else item for item in result]
        return result
    client.execute_command = bytes_command
    monkeypatch.setattr(valkey.asyncio, 'Valkey', lambda **kwargs: client)
    settings = Settings.from_env({'OPENAI_API_KEY': 'test', 'RATE_LIMIT_ENABLED': 'false',
        'CACHE_BACKEND': 'valkey'})
    async def run():
        cache = await create_cache(settings, 'fixture', None, tmp_path)
        await cache.store('Date?', [1., 0.], 'Tomorrow [Page 1]', ['Page 1'])
        assert (await cache.exact('Date?')).sources == ['Page 1']
        assert (await cache.check('When?', [1., .01])).answer == 'Tomorrow [Page 1]'
        await cache.shutdown()
    asyncio.run(run())
