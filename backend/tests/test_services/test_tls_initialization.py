"""Certificate preparation remains bounded, cancellable to wait, and TLS-safe."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import ssl
import threading
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.services.llm import gateway as module


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.fixture
def certificate_worker(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1)
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(module, "_SSL_WORKERS", executor)
    monkeypatch.setattr(module, "_SSL_SLOTS", slots)
    yield slots
    executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_certificate_loading_does_not_block_loop_and_cancel_keeps_real_capacity(
    monkeypatch, certificate_worker,
):
    started = threading.Event()
    release = threading.Event()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    calls = []

    def slow_loader():
        calls.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return context

    monkeypatch.setattr(httpx, "create_ssl_context", slow_loader)
    first = asyncio.create_task(module._load_ssl_context())
    queued = None
    try:
        await wait_until(started.is_set)
        # This loop callback must execute while the synchronous loader is blocked.
        assert len(calls) == 1 and calls[0] != threading.get_ident()
        queued = asyncio.create_task(module._load_ssl_context())
        await asyncio.sleep(0.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(RuntimeError, match="initialization is busy"):
            await module._load_ssl_context()
        assert len(calls) == 1

        # Queued cancellation releases only its own, never-started slot.
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert certificate_worker.acquire(blocking=False)
        assert not certificate_worker.acquire(blocking=False)
        certificate_worker.release()
    finally:
        release.set()
        if queued and not queued.done():
            queued.cancel()
        await asyncio.gather(first, *([queued] if queued else []), return_exceptions=True)
    await wait_until(lambda: certificate_worker._value == 2)
    assert await module._load_ssl_context() is context
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_initialization_timeout_never_constructs_late_sdk_client(
    monkeypatch, certificate_worker, provider,
):
    started = threading.Event()
    release = threading.Event()

    def slow_loader():
        started.set()
        assert release.wait(3)
        raise ValueError("Late certificate load failure")

    sdk = importlib.import_module(provider)
    factory_name = "AsyncOpenAI" if provider == "openai" else "AsyncAnthropic"
    factory = Mock(side_effect=AssertionError("Must not construct a client after timeout"))
    monkeypatch.setattr(sdk, factory_name, factory)
    monkeypatch.setattr(httpx, "create_ssl_context", slow_loader)
    gateway = module.LLMGateway.from_profile({
        "provider": provider, "api_key": "fake-key", "model": "fake-model",
        "base_url": "https://example.invalid/v1",
    })
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _, context: errors.append(context))
    try:
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.03):
                await gateway.chat([{"role": "user", "content": "Offline test"}], _max_retries=0)
        assert started.is_set()
        factory.assert_not_called()
        assert gateway._client is None
        assert gateway.usage["request_attempts"] == 0
        release.set()
        await wait_until(lambda: certificate_worker._value == 2)
        await asyncio.sleep(0.02)
        factory.assert_not_called()
        assert not errors
    finally:
        release.set()
        loop.set_exception_handler(previous)


@pytest.mark.asyncio
async def test_httpx_original_ca_environment_branches_are_preserved(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    calls = []
    monkeypatch.setattr(ssl, "create_default_context", lambda **kwargs: calls.append(kwargs) or context)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    import certifi
    monkeypatch.setattr(certifi, "where", lambda: "synthetic-certifi.pem")
    assert await module._load_ssl_context() is context
    monkeypatch.setenv("SSL_CERT_DIR", "synthetic-ca-dir")
    assert await module._load_ssl_context() is context
    monkeypatch.setenv("SSL_CERT_FILE", "synthetic-user-ca.pem")
    assert await module._load_ssl_context() is context
    assert calls == [
        {"cafile": "synthetic-certifi.pem"},
        {"capath": "synthetic-ca-dir"},
        {"cafile": "synthetic-user-ca.pem"},
    ]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_sdk_default_http_client_keeps_proxy_and_uses_one_trust_context(monkeypatch, provider):
    sdk = importlib.import_module(provider)
    http_base = next(base for base in sdk.DefaultAsyncHttpxClient.__mro__
                     if base.__name__ == "AsyncClient")
    sdk_http_name = http_base.__module__.split(".", 1)[0]
    sdk_transport = importlib.import_module(sdk_http_name + "._transports.default")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    loader = AsyncMock(return_value=context)
    monkeypatch.setattr(module, "_load_ssl_context", loader)
    # A synthetic environment proxy exercises proxy mounts without reading users' settings.
    monkeypatch.setitem(
        http_base._get_proxy_map.__globals__, "get_environment_proxies",
        lambda: {"https://": "http://proxy.invalid:8888"},
    )
    sdk_globals = sdk.DefaultAsyncHttpxClient.__init__.__globals__
    if "get_environment_proxies" in sdk_globals:
        monkeypatch.setitem(
            sdk_globals, "get_environment_proxies",
            lambda: {"https://": "http://proxy.invalid:8888"},
        )
    original_context_factory = sdk_transport.create_ssl_context
    contexts = []

    def track_context(*args, **kwargs):
        contexts.append(kwargs.get("verify"))
        return original_context_factory(*args, **kwargs)

    monkeypatch.setattr(sdk_transport, "create_ssl_context", track_context)
    http_clients = []
    factory_name = "AsyncOpenAI" if provider == "openai" else "AsyncAnthropic"

    def reject_sdk(**kwargs):
        http_clients.append(kwargs["http_client"])
        raise ValueError("Synthetic SDK construction failure")

    monkeypatch.setattr(sdk, factory_name, reject_sdk)
    gateway = module.LLMGateway.from_profile({
        "provider": provider, "api_key": "fake-key", "model": "fake-model",
        "base_url": "https://example.invalid/v1",
    })
    call = gateway._chat_openai_once if provider == "openai" else gateway._chat_anthropic
    with pytest.raises(ValueError, match="Synthetic SDK"):
        await call([], None, 0.1, 10)
    loader.assert_awaited_once()
    # Some SDK versions also build proxy mounts before invoking HTTPX itself.
    assert len(contexts) >= 2 and all(item is context for item in contexts)
    client = http_clients[0]
    assert client.trust_env is True
    assert client.is_closed  # The SDK never took ownership, so the gateway closes it.
    assert any(transport is not None for transport in client._mounts.values())
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
