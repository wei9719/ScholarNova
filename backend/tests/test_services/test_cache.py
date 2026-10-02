"""Cache isolation, bounds and Redis failure tests; no real Redis/network calls."""

import asyncio
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import cache as module
from app.core.cache import CacheManager, InMemoryCache


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(module, "_global_memory_cache", None)
    monkeypatch.setattr(module, "_shared_backends", weakref.WeakKeyDictionary())
    monkeypatch.setattr(module.settings, "REDIS_URL", None)


def fake_redis(monkeypatch, **overrides):
    client = SimpleNamespace(
        ping=AsyncMock(return_value=True),
        get=AsyncMock(return_value='{"answer": 42}'),
        setex=AsyncMock(),
        delete=AsyncMock(return_value=1),
        exists=AsyncMock(return_value=1),
        aclose=AsyncMock(),
    )
    for name, value in overrides.items():
        setattr(client, name, value)
    factory = Mock(return_value=client)
    monkeypatch.setattr(module, "REDIS_AVAILABLE", True)
    monkeypatch.setattr(module, "redis", SimpleNamespace(from_url=factory))
    return client, factory


@pytest.mark.asyncio
async def test_no_redis_uses_shared_memory_and_preserves_json_values():
    writer, reader = CacheManager(), CacheManager()
    for number, value in enumerate([[], {}, None, False, 0, "", {"text": "文献"}]):
        assert await writer.set(str(number), value)
        assert await reader.exists(str(number))
        assert await reader.get(str(number)) == value
    assert writer._use_memory and reader._use_memory
    assert writer._client is reader._client


@pytest.mark.asyncio
async def test_borrower_close_does_not_clear_other_search_results():
    first, second = CacheManager(), CacheManager()
    await first.set("search:1", ["paper"])
    await second.close()
    await first.close()
    assert await second.get("search:1") == ["paper"]
    await CacheManager.close_all()
    assert await second.get("search:1") is None


@pytest.mark.asyncio
async def test_prefix_clear_removes_multiple_keys_and_returns_count():
    cache = CacheManager()
    for number in range(300):
        assert await cache.set(f"search:{number}", number)
    assert await cache.set("other:keep", True)
    assert await cache.clear_prefix("search:") == 300
    assert await cache.clear_prefix("search:") == 0
    assert await cache.get("other:keep") is True


@pytest.mark.asyncio
async def test_memory_lru_entry_limit():
    cache = InMemoryCache(max_entries=2)
    await cache.setex("a", 60, "one")
    await cache.setex("b", 60, "two")
    assert await cache.get("a") == "one"
    await cache.setex("c", 60, "three")
    assert await cache.get("b") is None
    assert list(cache._store) == ["a", "c"]
    assert len(cache._store) == 2


@pytest.mark.asyncio
async def test_memory_byte_limit_counts_utf8_and_replacement():
    cache = InMemoryCache(max_entries=20, max_bytes=10)
    await cache.setex("a", 60, "一二")
    assert cache._bytes == 7
    await cache.setex("b", 60, "三四")
    assert await cache.get("a") is None
    assert cache._bytes == 7
    await cache.setex("b", 60, "x")
    assert cache._bytes == 2
    with pytest.raises(ValueError, match="budget"):
        await cache.setex("b", 60, "一二三四")
    assert await cache.get("b") == "x"
    assert await cache.delete("b", "b", "missing") == 1
    assert cache._bytes == 0


@pytest.mark.asyncio
async def test_expired_unread_entries_are_reclaimed_on_write_and_scan(monkeypatch):
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    cache = InMemoryCache()
    await cache.setex("old:1", 5, "a")
    await cache.setex("old:2", 5, "b")
    clock.now = 16.0
    await cache.setex("new", 5, "c")
    assert list(cache._store) == ["new"]
    clock.now = 22.0
    assert [key async for key in cache.scan_iter("*")] == []
    assert cache._bytes == 0


@pytest.mark.asyncio
async def test_expired_get_and_delete_do_not_report_stale_values(monkeypatch):
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    cache = InMemoryCache()
    await cache.setex("one", 2, "x")
    await cache.setex("two", 2, "x")
    clock.now = 12.0
    assert await cache.get("one") is None
    assert await cache.delete("two") == 0


@pytest.mark.asyncio
async def test_invalid_ttl_and_oversized_payload_fail_without_clearing_good_data(monkeypatch):
    monkeypatch.setattr(module, "_global_memory_cache", InMemoryCache(max_bytes=100))
    cache = CacheManager()
    assert await cache.set("good", 1)
    assert not await cache.set("zero", 2, ttl=0)
    assert not await cache.set("negative", 2, ttl=-1)
    assert not await cache.set("huge", "x" * 101)
    assert await cache.get("good") == 1


@pytest.mark.asyncio
async def test_configured_redis_is_used_and_shared_across_managers(monkeypatch):
    client, factory = fake_redis(monkeypatch)
    first, second = CacheManager("redis://fake/0"), CacheManager("redis://fake/0")
    assert await first.get("item") == {"answer": 42}
    assert await second.exists("item")
    assert not first._use_memory and not second._use_memory
    factory.assert_called_once()
    assert factory.call_args.kwargs["max_connections"] == 16
    assert factory.call_args.kwargs["socket_timeout"] == 1.0
    await first.close()
    client.aclose.assert_not_awaited()
    assert await second._get_client() is client
    await CacheManager.close_all()
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_200_concurrent_initializers_open_only_one_redis_pool(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def ping():
        entered.set()
        await release.wait()
        return True

    client, factory = fake_redis(monkeypatch, ping=AsyncMock(side_effect=ping))
    managers = [CacheManager("redis://fake/0") for _ in range(200)]
    tasks = [asyncio.create_task(manager._get_client()) for manager in managers]
    await entered.wait()
    await asyncio.sleep(0)
    assert factory.call_count == 1
    release.set()
    assert all(item is client for item in await asyncio.gather(*tasks))
    client.ping.assert_awaited_once()
    await CacheManager.close_all()


@pytest.mark.asyncio
async def test_redis_failure_closes_failed_connection_and_shares_fallback(monkeypatch):
    client, factory = fake_redis(monkeypatch, ping=AsyncMock(side_effect=OSError("offline")))
    first, second = CacheManager("redis://fake/0"), CacheManager("redis://fake/0")
    assert await first.set("search:1", [1])
    assert await second.get("search:1") == [1]
    assert first._use_memory and second._use_memory
    factory.assert_called_once()
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_timeout_is_bounded_and_does_not_block_other_coroutines(monkeypatch):
    never = asyncio.Event()
    client, _ = fake_redis(monkeypatch, ping=AsyncMock(side_effect=never.wait))
    cache = CacheManager("redis://fake/0", operation_timeout=0.02)
    heartbeat = asyncio.Event()

    async def tick():
        await asyncio.sleep(0)
        heartbeat.set()

    await asyncio.wait_for(asyncio.gather(cache.set("a", 1), tick()), timeout=0.5)
    assert heartbeat.is_set()
    assert cache._use_memory
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_redis_failure_switches_all_managers_once(monkeypatch):
    client, factory = fake_redis(monkeypatch, setex=AsyncMock(side_effect=OSError("lost")))
    first, second = CacheManager("redis://fake/0"), CacheManager("redis://fake/0")
    await second._get_client()
    assert await first.set("saved", {"paper": 1})
    assert await second.get("saved") == {"paper": 1}
    assert second._use_memory
    assert factory.call_count == 1
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_redis_timeout_preserves_new_write_in_memory(monkeypatch):
    never = asyncio.Event()

    async def hang(*_args):
        await never.wait()

    client, _ = fake_redis(monkeypatch, setex=AsyncMock(side_effect=hang))
    cache = CacheManager("redis://fake/0", operation_timeout=0.02)
    assert await asyncio.wait_for(cache.set("saved", 3), timeout=0.5)
    assert await cache.get("saved") == 3
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_initialization_closes_candidate_and_allows_new_initializer(monkeypatch):
    entered = asyncio.Event()

    async def hang():
        entered.set()
        await asyncio.Event().wait()

    client, factory = fake_redis(monkeypatch, ping=AsyncMock(side_effect=hang))
    cache = CacheManager("redis://fake/0")
    task = asyncio.create_task(cache._get_client())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    client.aclose.assert_awaited_once()
    replacement = SimpleNamespace(ping=AsyncMock(return_value=True), aclose=AsyncMock())
    factory.return_value = replacement
    assert await cache._get_client() is replacement
    assert not cache._use_memory
    await CacheManager.close_all()


@pytest.mark.asyncio
async def test_cancel_operation_is_not_swallowed_or_converted_to_memory(monkeypatch):
    started = asyncio.Event()

    async def hang(*_args):
        started.set()
        await asyncio.Event().wait()

    client, _ = fake_redis(monkeypatch, get=AsyncMock(side_effect=hang))
    cache = CacheManager("redis://fake/0")
    task = asyncio.create_task(cache.get("a"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not cache._use_memory
    client.aclose.assert_not_awaited()
    await CacheManager.close_all()


@pytest.mark.asyncio
async def test_missing_redis_package_falls_back_without_error(monkeypatch):
    monkeypatch.setattr(module, "REDIS_AVAILABLE", False)
    manager = CacheManager("redis://fake/0")
    assert isinstance(await manager._get_client(), InMemoryCache)
    assert manager._use_memory


@pytest.mark.asyncio
async def test_redis_urls_keep_distinct_connection_pools(monkeypatch):
    first = SimpleNamespace(ping=AsyncMock(return_value=True), aclose=AsyncMock())
    second = SimpleNamespace(ping=AsyncMock(return_value=True), aclose=AsyncMock())
    _, factory = fake_redis(monkeypatch)
    factory.side_effect = [first, second]
    assert await CacheManager("redis://fake/0")._get_client() is first
    assert await CacheManager("redis://fake/1")._get_client() is second
    await CacheManager.close_all()
    first.aclose.assert_awaited_once()
    second.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_prefix_redis_batches_are_bounded(monkeypatch):
    async def scan(pattern):
        for index in range(300):
            yield f"{pattern[:-1]}{index}"

    client, _ = fake_redis(monkeypatch, scan_iter=scan)
    client.delete.side_effect = lambda *keys: len(keys)
    manager = CacheManager("redis://fake/0")
    assert await manager.clear_prefix("search:") == 300
    assert [len(call.args) for call in client.delete.call_args_list] == [128, 128, 44]
    await CacheManager.close_all()


@pytest.mark.asyncio
async def test_shutdown_releases_pools_and_next_lifespan_can_initialize(monkeypatch):
    client, factory = fake_redis(monkeypatch)
    manager = CacheManager("redis://fake/0")
    await manager._get_client()
    await CacheManager.close_all()
    await CacheManager.close_all()
    assert not module._shared_backends
    client.aclose.assert_awaited_once()
    assert await manager._get_client() is client
    assert factory.call_count == 2
    await CacheManager.close_all()
