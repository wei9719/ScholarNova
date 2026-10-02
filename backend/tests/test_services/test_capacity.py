"""Deterministic single-process admission tests; all upstream work is fake."""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from app.core.capacity import (
    CapacityExceeded,
    ExpensiveRequestMiddleware,
    WorkPool,
    busy_response,
)


async def wait_until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(poll(), timeout=2)


def assert_empty(pool):
    assert pool.reserved == pool.active == 0
    assert pool.snapshot()["queued"] == 0
    assert pool._semaphore._value == pool.limit


@pytest.mark.parametrize("active, queued, wait", [(0, 0, 1), (1, -1, 1), (1, 1, 0)])
def test_invalid_capacity_rejected(active, queued, wait):
    with pytest.raises(ValueError):
        WorkPool(active, queued, wait)


def test_busy_response_has_retry_metadata():
    response = busy_response()
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "2"
    assert json.loads(response.body)["code"] == "SERVICE_BUSY"


@pytest.mark.asyncio
async def test_100_request_burst_bounds_active_waiting_and_skips_overflow():
    pool = WorkPool(active=4, queued=6, wait_seconds=2)
    release = asyncio.Event()
    entered = 0
    replies = []

    async def upstream(scope, receive, send):
        nonlocal entered
        entered += 1
        assert pool.active <= 4
        await release.wait()
        await JSONResponse({"ok": True})(scope, receive, send)

    app = ExpensiveRequestMiddleware(upstream, pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def request():
            response = await client.post("/api/v1/agent/chat", json={"question": "fake"})
            replies.append(response)
            return response

        tasks = [asyncio.create_task(request()) for _ in range(100)]
        try:
            await wait_until(lambda: entered == 4 and len(replies) == 90)
            snapshot = pool.snapshot()
            assert snapshot["active"] == 4
            assert snapshot["queued"] == 6
            assert pool.reserved == 10
            assert all(response.status_code == 429 for response in replies)
            assert all(response.headers["Retry-After"] == "2" for response in replies)
            # Health/status reads remain responsive while AI is saturated.
            release.set()
            responses = await asyncio.gather(*tasks)
            assert sum(response.status_code == 200 for response in responses) == 10
            assert sum(response.status_code == 429 for response in responses) == 90
            assert entered == 10
            assert pool.peak_active == 4
            assert pool.rejected == 90
            assert_empty(pool)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_saturated_pool_does_not_gate_status_cancel_or_unrelated_routes():
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    lease = pool.reserve()
    await lease.__aenter__()
    inner = 0

    async def upstream(scope, receive, send):
        nonlocal inner
        inner += 1
        await JSONResponse({"responsive": True})(scope, receive, send)

    app = ExpensiveRequestMiddleware(upstream, pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for method, path in [
            ("GET", "/api/v1/health"),
            ("GET", "/api/v1/search/run-id"),
            ("DELETE", "/api/v1/search/run-id"),
            ("POST", "/api/v1/search"),
        ]:
            response = await client.request(method, path)
            assert response.status_code == 200
        assert inner == 4
        assert (await client.post("/api/v1/agent/chat")).status_code == 429
        assert inner == 4
    lease.release()
    assert_empty(pool)


@pytest.mark.parametrize("path", [
    "/api/v1/agent/chat", "/api/v1/model/test", "/api/v1/knowledge",
    "/api/v1/knowledge/ai-analyze", "/api/v1/knowledge/recommend",
    "/api/v1/knowledge/routes/123/ai-generate", "/api/v1/papers/123/analyze",
    "/api/v1/papers/123/fulltext", "/api/v1/papers/123/translate",
])
def test_expensive_routes_are_covered(path):
    assert ExpensiveRequestMiddleware.applies({"type": "http", "method": "POST", "path": path})


@pytest.mark.asyncio
async def test_wait_timeout_does_not_run_work_and_releases_reservation():
    pool = WorkPool(active=1, queued=1, wait_seconds=0.02)
    running = pool.reserve()
    await running.__aenter__()
    waiting = pool.reserve()
    with pytest.raises(CapacityExceeded):
        await waiting.__aenter__()
    assert pool.active == pool.reserved == 1
    assert pool.timed_out == 1
    assert waiting.released
    waiting.release()
    running.release()
    assert_empty(pool)


@pytest.mark.asyncio
async def test_cancel_waiter_and_repeated_release_leave_no_leaks():
    pool = WorkPool(active=1, queued=1, wait_seconds=1)
    running = pool.reserve()
    await running.__aenter__()
    waiting = pool.reserve()
    task = asyncio.create_task(waiting.__aenter__())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.reserved == pool.active == 1
    waiting.release()
    waiting.release()
    running.release()
    running.release()
    assert_empty(pool)


@pytest.mark.asyncio
async def test_reserved_but_never_started_lease_can_be_released():
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    lease = pool.reserve()
    lease.release()
    lease.release()
    assert_empty(pool)
    async with pool.reserve():
        assert pool.active == 1
    assert_empty(pool)


@pytest.mark.asyncio
async def test_application_exception_releases_active_slot():
    pool = WorkPool(active=1, queued=0, wait_seconds=1)

    async def failed_app(_scope, _receive, _send):
        raise RuntimeError("fake upstream failure")

    middleware = ExpensiveRequestMiddleware(failed_app, pool)
    with pytest.raises(RuntimeError, match="fake upstream"):
        await middleware({"type": "http", "method": "POST", "path": "/api/v1/agent/chat"}, None, None)
    assert_empty(pool)


@pytest.mark.asyncio
async def test_active_request_cancellation_releases_slot():
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    started = asyncio.Event()

    async def blocked_app(_scope, _receive, _send):
        started.set()
        await asyncio.Event().wait()

    middleware = ExpensiveRequestMiddleware(blocked_app, pool)
    task = asyncio.create_task(middleware(
        {"type": "http", "method": "POST", "path": "/api/v1/agent/chat"}, None, None
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_empty(pool)
