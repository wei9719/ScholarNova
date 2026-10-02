"""Fan-out bounds protect each admitted search job as well as the job pool."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.api.v1 import search
from app.models.search_run import SearchRun
from app.schemas.query import DataSource, SubQuery
from app.schemas.search import SearchRequest
from app.services.search import retriever as module
from app.services.search.retriever import MAX_SOURCE_REQUESTS, Retriever, SourceStatus


@pytest.fixture(autouse=True)
def isolate_circuits(monkeypatch):
    monkeypatch.setattr(module, "_SOURCE_FAILURE_COUNTS", {})
    monkeypatch.setattr(module, "_SOURCE_CIRCUIT_UNTIL", {})


def subquery(index, source=DataSource.CROSSREF):
    return SubQuery(query=f"mock query {index}", source=source, rationale="unit test")


def test_1000_duplicate_sources_rejected_before_search_planning():
    with pytest.raises(ValidationError) as caught:
        SearchRequest(query="fake", sources=["crossref"] * 1000)
    assert any(error["loc"] == ("sources",) and error["type"] == "too_long" for error in caught.value.errors())


@pytest.mark.asyncio
async def test_api_rejects_1000_sources_without_starting_worker(client, monkeypatch):
    starter = Mock()
    monkeypatch.setattr(search, "_start_search_task", starter)
    response = await client.post("/api/v1/search", json={"query": "fake", "sources": ["crossref"] * 1000})
    assert response.status_code == 422
    starter.assert_not_called()


def test_legal_source_duplicates_keep_first_occurrence_order():
    request = SearchRequest(query="fake", sources=["openalex", "crossref", "openalex"])
    assert request.sources == [DataSource.OPENALEX, DataSource.CROSSREF]
    assert SearchRequest(query="fake", sources=list(DataSource)).sources == list(DataSource)


@pytest.mark.asyncio
async def test_400_internal_subqueries_never_spawn_more_than_12_upstream_tasks(caplog):
    active = 0
    peak = 0
    calls = []
    release = asyncio.Event()
    ready = asyncio.Event()
    reported = []

    async def source_search(query, _max_results):
        nonlocal active, peak
        calls.append(query)
        active += 1
        peak = max(peak, active)
        if active == MAX_SOURCE_REQUESTS:
            ready.set()
        try:
            await release.wait()
            return []
        finally:
            active -= 1

    async def report(status):
        reported.append(status)

    source = SimpleNamespace(name="fake_crossref", search=source_search, last_error=None)
    retriever = Retriever(sources={DataSource.CROSSREF: source})
    task = asyncio.create_task(retriever.retrieve(
        [subquery(index) for index in range(400)], progress_callback=report,
    ))
    try:
        await asyncio.wait_for(ready.wait(), timeout=1)
        await asyncio.sleep(0)
        assert active == peak == len(calls) == MAX_SOURCE_REQUESTS
        release.set()
        result = await task
        assert active == 0
        assert len(calls) == 12
        assert result.successful_sources == 12
        limit_status, = [status for status in result.source_statuses if status.source == "retrieval_limit"]
        assert not limit_status.success
        assert "388" in limit_status.error
        assert "仅覆盖已执行部分" in limit_status.error
        assert limit_status in reported
        assert "388" in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_duplicate_subqueries_do_not_use_up_cap_or_call_upstream_again():
    calls = []

    async def source_search(query, _max_results):
        calls.append(query)
        return []

    source = SimpleNamespace(name="fake_crossref", search=source_search, last_error=None)
    retriever = Retriever(sources={DataSource.CROSSREF: source})
    queries = [subquery(0)] * 1000 + [subquery(1), SubQuery(query=" mock query 1 ", source=DataSource.CROSSREF, rationale="duplicate")]
    result = await retriever.retrieve(queries)
    assert calls == ["mock query 0", "mock query 1"]
    assert len(result.source_statuses) == 2
    assert all(status.success for status in result.source_statuses)


@pytest.mark.asyncio
async def test_same_query_on_distinct_sources_is_not_deduplicated_and_failure_isolated():
    called = []

    async def success(query, _max_results):
        called.append(("crossref", query))
        return []

    async def failure(query, _max_results):
        called.append(("openalex", query))
        raise OSError("fake source unavailable")

    retriever = Retriever(sources={
        DataSource.CROSSREF: SimpleNamespace(name="fake_crossref", search=success, last_error=None),
        DataSource.OPENALEX: SimpleNamespace(name="fake_openalex", search=failure, last_error=None),
    })
    result = await retriever.retrieve([subquery(0), subquery(0, DataSource.OPENALEX)])
    assert len(called) == 2
    assert result.successful_sources == result.failed_sources == 1
    assert {status.source for status in result.source_statuses} == {"fake_crossref", "fake_openalex"}


def test_local_limit_notice_is_not_counted_as_an_api_request():
    statuses = [
        SourceStatus(source="crossref", success=True),
        SourceStatus(source="openalex", success=False),
        SourceStatus(source="retrieval_limit", success=False, error="跳过 388 个超限子查询"),
    ]
    assert search._api_call_count(statuses) == 2
    notice = search._status_call(statuses[-1])
    assert notice["label"] == "检索容量保护"
    assert notice["api_name"] == "本地策略 / 未请求外部 API"
    assert notice["endpoint"] == ""
    assert not notice["success"]
    assert "388" in notice["error"]


@pytest.mark.asyncio
async def test_search_response_keeps_limit_notice_but_excludes_it_from_api_failure_metrics():
    statuses = [
        SourceStatus(source="crossref", success=True),
        SourceStatus(source="openalex", success=False, error="fake upstream failure"),
        SourceStatus(source="retrieval_limit", success=False, error="跳过 388 个超限子查询"),
    ]
    run = SearchRun(
        id="fake-run", raw_query="fake", status="failed",
        created_at=datetime(2026, 10, 2),
        source_status={
            "calls": [search._status_call(status) for status in statuses],
            "api_calls": search._api_call_count(statuses),
        },
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=Mock(return_value=run))))
    response = await search.get_search_run("fake-run", db)
    assert response.runtime_metrics["api_calls"] == 2
    assert response.runtime_metrics["successful_calls"] == 1
    assert response.runtime_metrics["failed_calls"] == 1
    assert response.source_status[-1]["label"] == "检索容量保护"
