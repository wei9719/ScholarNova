"""Search background-job lifetime and child-request cancellation; no live sources."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import pytest_asyncio

from app.api.v1 import search
from app.config import settings
from app.core.capacity import WorkPool
from app.models.search_run import SearchRun
from app.schemas.query import DataSource, SubQuery
from app.schemas.search import SearchRequest
from app.services.search.retriever import Retriever


@pytest_asyncio.fixture(autouse=True)
async def isolate_jobs(monkeypatch):
    real_fail_search = search._fail_search
    monkeypatch.setattr(search, "_running_search_tasks", set())
    monkeypatch.setattr(search, "_fail_search", AsyncMock())
    monkeypatch.setattr(search, "_execute_search_task", AsyncMock())
    monkeypatch.setattr(settings, "SEARCH_TIMEOUT", 2)
    yield real_fail_search
    await search.shutdown_search_tasks()


def request():
    return SearchRequest(query="local mocked research", sources=[DataSource.CROSSREF])


def assert_empty(pool):
    assert pool.active == pool.reserved == 0
    assert pool._semaphore._value == pool.limit


@pytest.mark.asyncio
async def test_bounded_search_success_releases_lease():
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    await search._bounded_search("run", request(), pool.reserve())
    search._execute_search_task.assert_awaited_once()
    search._fail_search.assert_not_awaited()
    assert_empty(pool)


@pytest.mark.asyncio
async def test_search_queue_timeout_marks_failed_without_starting_upstream():
    pool = WorkPool(active=1, queued=1, wait_seconds=0.02)
    running = pool.reserve()
    await running.__aenter__()
    await search._bounded_search("waiting", request(), pool.reserve())
    search._execute_search_task.assert_not_awaited()
    search._fail_search.assert_awaited_once()
    assert "排队超时" in search._fail_search.call_args.args[1]
    assert pool.active == pool.reserved == 1
    running.release()
    assert_empty(pool)


@pytest.mark.asyncio
async def test_total_search_timeout_cancels_worker_and_marks_failed(monkeypatch):
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    cancelled = asyncio.Event()

    async def blocked(*_args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(search, "_execute_search_task", blocked)
    monkeypatch.setattr(settings, "SEARCH_TIMEOUT", 0.02)
    await search._bounded_search("timeout", request(), pool.reserve())
    assert cancelled.is_set()
    assert "总时限" in search._fail_search.call_args.args[1]
    assert_empty(pool)


@pytest.mark.asyncio
async def test_queued_search_cancel_releases_slot_without_starting_worker():
    pool = WorkPool(active=1, queued=1, wait_seconds=1)
    active = pool.reserve()
    await active.__aenter__()
    search._start_search_task("queued", request(), pool.reserve())
    await asyncio.sleep(0)
    await search.shutdown_search_tasks()
    search._execute_search_task.assert_not_awaited()
    assert search._fail_search.await_count >= 1
    assert not search._running_search_tasks
    assert pool.active == pool.reserved == 1
    active.release()
    assert_empty(pool)


@pytest.mark.asyncio
async def test_cancel_before_coroutine_starts_still_releases_slot():
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    search._start_search_task("never-started", request(), pool.reserve())
    # No scheduling checkpoint: cancel the task before __aenter__ can execute.
    task, = search._running_search_tasks
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    search._execute_search_task.assert_not_awaited()
    assert not search._running_search_tasks
    assert_empty(pool)


@pytest.mark.asyncio
async def test_shutdown_before_coroutine_starts_also_marks_persisted_run_failed():
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    search._start_search_task("never-started", request(), pool.reserve())
    # shutdown cancels immediately, before any search coroutine can execute.
    await search.shutdown_search_tasks()
    search._execute_search_task.assert_not_awaited()
    search._fail_search.assert_awaited_once_with("never-started", "搜索已取消或应用正在关闭")
    assert not search._running_search_tasks
    assert_empty(pool)


@pytest.mark.asyncio
async def test_shutdown_cancels_active_and_queued_jobs_and_is_idempotent(monkeypatch):
    pool = WorkPool(active=2, queued=3, wait_seconds=1)
    started = []
    stopped = []
    ready = asyncio.Event()

    async def blocked(run_id, _request):
        started.append(run_id)
        if len(started) == 2:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(run_id)

    monkeypatch.setattr(search, "_execute_search_task", blocked)
    for index in range(5):
        search._start_search_task(str(index), request(), pool.reserve())
    await asyncio.wait_for(ready.wait(), timeout=1)
    assert pool.active == 2
    assert pool.snapshot()["queued"] == 3
    await search.shutdown_search_tasks()
    await search.shutdown_search_tasks()
    assert sorted(stopped) == sorted(started)
    assert len(started) == 2
    assert not search._running_search_tasks
    assert {call.args[0] for call in search._fail_search.await_args_list} == {str(i) for i in range(5)}
    assert_empty(pool)


@pytest.mark.asyncio
async def test_shutdown_status_write_failure_does_not_skip_other_runs(monkeypatch, caplog):
    pool = WorkPool(active=1, queued=1, wait_seconds=1)
    monkeypatch.setattr(search, "_fail_search", AsyncMock(side_effect=OSError("fake DB unavailable")))
    search._start_search_task("first", request(), pool.reserve())
    search._start_search_task("second", request(), pool.reserve())
    await search.shutdown_search_tasks()
    assert {call.args[0] for call in search._fail_search.await_args_list} == {"first", "second"}
    assert "Could not persist search shutdown status" in caplog.text
    assert not search._running_search_tasks
    assert_empty(pool)


@pytest.mark.asyncio
async def test_failed_status_write_does_not_leak_lease(monkeypatch):
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    monkeypatch.setattr(search, "_execute_search_task", AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(search, "_fail_search", AsyncMock(side_effect=OSError("fake database outage")))
    with pytest.raises(OSError, match="fake database"):
        await search._bounded_search("fail", request(), pool.reserve())
    assert_empty(pool)


@pytest.mark.asyncio
async def test_search_overflow_does_not_write_database_or_start_job(monkeypatch):
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    lease = pool.reserve()
    monkeypatch.setattr(search, "check_rate_limit", Mock(return_value=None))
    starter = Mock()
    monkeypatch.setattr(search, "_start_search_task", starter)
    http_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(search_capacity=pool)))
    db = SimpleNamespace(add=Mock(), commit=AsyncMock())
    response = await search.create_search(request(), http_request, db)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "2"
    starter.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    lease.release()
    assert_empty(pool)


@pytest.mark.asyncio
async def test_create_search_database_failure_releases_reserved_slot(monkeypatch):
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    monkeypatch.setattr(search, "check_rate_limit", Mock(return_value=None))
    starter = Mock()
    monkeypatch.setattr(search, "_start_search_task", starter)
    http_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(search_capacity=pool)))
    db = SimpleNamespace(add=Mock(), commit=AsyncMock(side_effect=OSError("fake DB unavailable")))
    with pytest.raises(OSError, match="fake DB"):
        await search.create_search(request(), http_request, db)
    starter.assert_not_called()
    assert_empty(pool)


@pytest.mark.asyncio
async def test_retriever_parent_cancellation_waits_for_all_child_source_cleanup():
    ready = asyncio.Event()
    entered = set()
    cleaned = set()

    def source(name):
        async def run(_query, _max_results):
            entered.add(name)
            if len(entered) == 2:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.add(name)
        return SimpleNamespace(name=name, search=run, last_error=None)

    retriever = Retriever(sources={
        DataSource.CROSSREF: source("test_crossref"),
        DataSource.OPENALEX: source("test_openalex"),
    })
    queries = [SubQuery(query="fake", source=item, rationale="unit test") for item in retriever.sources]
    task = asyncio.create_task(retriever.retrieve(queries))
    await asyncio.wait_for(ready.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned == entered == {"test_crossref", "test_openalex"}


@pytest.mark.asyncio
async def test_retriever_parent_deadline_cleans_sources_before_return():
    cleaned = asyncio.Event()

    async def blocked(_query, _max_results):
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    retriever = Retriever(sources={DataSource.CROSSREF: SimpleNamespace(
        name="test_source_deadline", search=blocked, last_error=None
    )})
    queries = [SubQuery(query="fake", source=DataSource.CROSSREF, rationale="unit test")]
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(retriever.retrieve(queries), timeout=0.02)
    assert cleaned.is_set()


def install_fake_session(monkeypatch, run):
    result = SimpleNamespace(scalar_one_or_none=Mock(return_value=run))
    db = SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock())
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = db
    monkeypatch.setattr("app.database.async_session_factory", factory)
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_fail_search_preserves_terminal_state_and_original_reason(
    monkeypatch, isolate_jobs, status
):
    completed_at = datetime(2026, 10, 1, 8, 30)
    progress = {"current_phase": status, "message": "原有完成说明", "total_papers": 12}
    run = SearchRun(
        id="terminal", raw_query="mock", status=status,
        error_message="原有失败原因" if status == "failed" else None,
        completed_at=completed_at, progress=dict(progress),
    )
    db = install_fake_session(monkeypatch, run)
    await isolate_jobs("terminal", "搜索已取消或应用正在关闭")
    assert run.status == status
    assert run.completed_at == completed_at
    assert run.progress == progress
    assert run.error_message == ("原有失败原因" if status == "failed" else None)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running"])
async def test_fail_search_exposes_safe_progress_message_without_internal_error(
    monkeypatch, isolate_jobs, status
):
    run = SearchRun(
        id="inflight", raw_query="mock", status=status,
        created_at=datetime(2026, 10, 1, 8, 30),
        error_message="test-only-private-upstream-error",
        progress={"current_phase": status, "total_papers": 3},
    )
    db = install_fake_session(monkeypatch, run)
    message = "搜索超过总时限，请缩小范围后重试"
    await isolate_jobs("inflight", message)
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error_message == message
    assert run.progress["current_phase"] == "failed"
    assert run.progress["total_papers"] == 3
    assert run.progress["message"] == message
    db.commit.assert_awaited_once()
    # Exercise actual response-schema serialization, not only the ORM field.
    detail = await search.get_search_run("inflight", db)
    payload = detail.model_dump(mode="json")
    assert payload["progress"]["message"] == message
    assert "test-only-private-upstream-error" not in str(payload)


@pytest.mark.asyncio
async def test_fail_search_ignores_missing_run(monkeypatch, isolate_jobs):
    db = install_fake_session(monkeypatch, None)
    await isolate_jobs("missing", "搜索已取消或应用正在关闭")
    db.commit.assert_not_awaited()
