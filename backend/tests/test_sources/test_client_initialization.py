"""Offline source-client lifecycle: no certificate I/O on the event loop."""

import asyncio
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.core import tls
from app.services.sources import base
from app.services.sources.openalex import OpenAlexSource


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


def fake_client(**_kwargs):
    client = Mock(is_closed=False)
    client.aclose = AsyncMock(side_effect=lambda: setattr(client, "is_closed", True))
    return client


async def test_parallel_initialization_reuses_one_client_and_close_once(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    async def load():
        entered.set()
        await release.wait()
        return context

    loader = AsyncMock(side_effect=load)
    client = fake_client()
    factory = Mock(return_value=client)
    monkeypatch.setattr(base, "load_ssl_context", loader)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    source = OpenAlexSource(timeout=1)
    jobs = [asyncio.create_task(source._get_client()) for _ in range(30)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert loader.await_count == 1 and factory.call_count == 0
        release.set()
        assert all(value is client for value in await asyncio.gather(*jobs))
        loader.assert_awaited_once()
        factory.assert_called_once()
        assert factory.call_args.kwargs["verify"] is context
        assert factory.call_args.kwargs["follow_redirects"] is True
        await asyncio.gather(source.close(), source.close())
        client.aclose.assert_awaited_once()
        assert source._client is None
    finally:
        release.set()
        await asyncio.gather(*jobs, return_exceptions=True)


async def test_close_during_initialization_invalidates_running_and_queued_calls(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    async def load():
        entered.set()
        await release.wait()
        return context

    monkeypatch.setattr(base, "load_ssl_context", load)
    factory = Mock(side_effect=fake_client)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    source = OpenAlexSource(timeout=1)
    first = asyncio.create_task(source._get_client())
    await asyncio.wait_for(entered.wait(), 1)
    queued = asyncio.create_task(source._get_client())
    await asyncio.sleep(0)
    await asyncio.wait_for(source.close(), 0.1)
    release.set()
    outcomes = await asyncio.gather(first, queued, return_exceptions=True)
    assert all(isinstance(value, RuntimeError) for value in outcomes)
    factory.assert_not_called()
    assert source._client is None
    # Closing an old operation does not permanently disable a reusable source.
    client = await source._get_client()
    await source.close()
    assert client.is_closed


@pytest.mark.parametrize("action", ["cancel", "timeout"])
async def test_slow_native_ca_loading_is_cancellable_without_late_client(monkeypatch, action):
    executor = ThreadPoolExecutor(max_workers=1)
    slots = threading.BoundedSemaphore(2)
    started, release = threading.Event(), threading.Event()
    worker_threads = []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def load():
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return context

    monkeypatch.setattr(tls, "SSL_WORKERS", executor)
    monkeypatch.setattr(tls, "SSL_SLOTS", slots)
    monkeypatch.setattr(httpx, "create_ssl_context", load)
    factory = Mock(side_effect=AssertionError("Late clients must never be constructed"))
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    source = OpenAlexSource(timeout=0.03 if action == "timeout" else 1)
    task = asyncio.create_task(source._get_client())
    try:
        await wait_until(started.is_set)
        assert worker_threads == [worker_threads[0]]
        assert worker_threads[0] != threading.get_ident()
        if action == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(task, 0.3)
        assert slots._value == 1  # Running native work still owns its real slot.
        await source.close()
        factory.assert_not_called()
        release.set()
        await wait_until(lambda: slots._value == 2)
        factory.assert_not_called()
        assert source._client is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        executor.shutdown(wait=True, cancel_futures=True)


async def test_cancelled_initializer_releases_lock_for_next_caller(monkeypatch):
    entered = asyncio.Event()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    async def load():
        entered.set()
        await asyncio.Event().wait()

    loader = AsyncMock(side_effect=load)
    monkeypatch.setattr(base, "load_ssl_context", loader)
    factory = Mock(side_effect=fake_client)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    source = OpenAlexSource(timeout=1)
    first = asyncio.create_task(source._get_client())
    await asyncio.wait_for(entered.wait(), 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    loader.side_effect, loader.return_value = None, context
    assert await asyncio.wait_for(source._get_client(), 0.1) is source._client
    factory.assert_called_once()
    await source.close()


async def test_initialization_budget_also_bounds_waiting_for_another_initializer(monkeypatch):
    loader = AsyncMock()
    monkeypatch.setattr(base, "load_ssl_context", loader)
    source = OpenAlexSource(timeout=0.01)
    await source._client_lock.acquire()
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(source._get_client(), 0.3)
        assert source._client_lock.locked()
        loader.assert_not_called()
    finally:
        source._client_lock.release()
        await source.close()


def test_gateway_and_sources_share_the_same_bounded_certificate_pool():
    from app.services.llm import gateway

    assert gateway._SSL_WORKERS is tls.SSL_WORKERS
    assert gateway._SSL_SLOTS is tls.SSL_SLOTS


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("private trust-store path")])
async def test_initialization_failure_is_not_reported_as_successful_empty_source(monkeypatch, error):
    from app.services.search.retriever import Retriever

    monkeypatch.setattr(base, "load_ssl_context", AsyncMock(side_effect=error))
    source = OpenAlexSource(timeout=1)
    papers, status = await Retriever(timeout=1)._retrieve_from_source(source, "offline", 1)
    assert papers == [] and status.success is False
    assert "initialization" in status.error
    assert "private" not in status.error
    await source.close()


async def test_source_keeps_tls_validation_and_environment_proxy_mounts(monkeypatch):
    import httpx._transports.default as transport

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    loader = AsyncMock(return_value=context)
    monkeypatch.setattr(base, "load_ssl_context", loader)
    monkeypatch.setitem(httpx.AsyncClient._get_proxy_map.__globals__, "get_environment_proxies",
                        lambda: {"https://": "http://proxy.invalid:8888"})
    original = transport.create_ssl_context
    contexts = []

    def track(*args, **kwargs):
        contexts.append(kwargs.get("verify"))
        return original(*args, **kwargs)

    monkeypatch.setattr(transport, "create_ssl_context", track)
    source = OpenAlexSource(base_url="https://example.invalid", timeout=1)
    client = await source._get_client()
    try:
        loader.assert_awaited_once()
        assert client.trust_env is True
        assert len(contexts) >= 2 and all(value is context for value in contexts)
        assert any(value is not None for value in client._mounts.values())
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    finally:
        await source.close()
    assert client.is_closed
