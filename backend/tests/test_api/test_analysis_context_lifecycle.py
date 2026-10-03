"""Remote analysis works from snapshots, not checked-out SQLite connections."""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import analysis, knowledge
from app.database import Base
from app.models.knowledge import KnowledgeBase, ResearchRoute
from app.models.paper import PaperEntity
from app.schemas.knowledge import AIAnalyzeRequest, RecommendRequest
from app.schemas.query import AnalysisRequest
from app.services import inference
from app.services.diagram import route_pipeline


@pytest_asyncio.fixture(params=[False, True], ids=["retained-orm", "expired-orm"])
async def small_pool(tmp_path, request):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'analysis-context.sqlite'}",
        pool_size=1, max_overflow=0, pool_timeout=0.1,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=request.param)
    async with sessions() as db:
        db.add(KnowledgeBase(id="note", title="Original note", category="Test", content="Original evidence",
                             research_points=["Evidence point"], tags=["Grounding"]))
        db.add(ResearchRoute(id="route", title="Original route", description="Original requirement",
                             knowledge_ids=["note"], status="active"))
        db.add(PaperEntity(id="paper", title="Test paper", abstract="Test abstract", source="test"))
        await db.commit()
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _another_request_reads(sessions):
    async with sessions() as other:
        assert await other.scalar(select(KnowledgeBase.title).where(KnowledgeBase.id == "note")) == "Original note"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["analysis", "recommendation", "route"])
async def test_remote_wait_releases_read_connection(small_pool, monkeypatch, endpoint):
    async with small_pool() as db:
        async def model(**kwargs):
            # One connection makes leaked checkouts deterministic, without load
            # generation or real API calls. Test both ORM expiry policies.
            await _another_request_reads(small_pool)
            assert not db.in_transaction()
            assert "Original" in kwargs["messages"][-1]["content"]
            return SimpleNamespace(content="Supported analysis", profile={"provider": "fake", "model": "fake"},
                                   fallback_used=False, usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

        monkeypatch.setattr(knowledge, "chat_with_fallback", model)
        monkeypatch.setattr(knowledge, "judge_architecture", AsyncMock(return_value=None))
        monkeypatch.setattr(inference, "chat_with_fallback", model)
        if endpoint == "route":
            stream = route_pipeline.stream_route_analysis("route", db)
            try:
                assert (await anext(stream))["progress"] == 10
                assert (await anext(stream))["data"]["text_analysis"] == "Supported analysis"
            finally:
                await stream.aclose()  # Do not run the image stages.
        elif endpoint == "analysis":
            result = await knowledge.ai_analyze_research(AIAnalyzeRequest(knowledge_ids=["note"]), db)
            assert result.model_completed
        else:
            result = await knowledge.recommend_papers(RecommendRequest(knowledge_ids=["note"]), db)
            assert result.model_completed
        assert not db.in_transaction()


@pytest.mark.asyncio
async def test_pdf_fetch_wait_releases_metadata_connection(small_pool, monkeypatch):
    monkeypatch.setattr(analysis, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analysis, "chat_with_fallback", AsyncMock(return_value=SimpleNamespace(
        content="Abstract evidence", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )))
    async with small_pool() as db:
        async def fetch_context(_paper_id, paper_info, _db):
            await _another_request_reads(small_pool)
            assert not db.in_transaction()
            assert paper_info["title"] == "Test paper"
            return "", [], "abstract", "No open access PDF"

        monkeypatch.setattr(analysis, "_load_document_context", fetch_context)
        result = await analysis.analyze_paper("paper", AnalysisRequest(query="Summarize"), None, db)
        assert result.model_completed


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["analysis", "recommendation", "route"])
async def test_cancelled_remote_wait_has_no_active_transaction(small_pool, monkeypatch, endpoint):
    entered = asyncio.Event()
    async with small_pool() as db:
        async def model(**_kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(knowledge, "chat_with_fallback", model)
        monkeypatch.setattr(inference, "chat_with_fallback", model)
        stream = None
        if endpoint == "route":
            stream = route_pipeline.stream_route_analysis("route", db)
            await anext(stream)
            call = anext(stream)
        elif endpoint == "analysis":
            call = knowledge.ai_analyze_research(AIAnalyzeRequest(knowledge_ids=["note"]), db)
        else:
            call = knowledge.recommend_papers(RecommendRequest(knowledge_ids=["note"]), db)
        task = asyncio.create_task(call)
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert not db.in_transaction()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            if stream:
                await stream.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["description", "title", "knowledge_ids", "status", "ai_analysis", "delete"])
async def test_route_cannot_publish_over_changed_or_deleted_snapshot(small_pool, change):
    async with small_pool() as db:
        snapshot = (await route_pipeline._resolve_route_context("route", db))["route"]
        assert not db.in_transaction()
        async with small_pool() as other:
            route = await other.get(ResearchRoute, "route")
            if change == "delete":
                await other.delete(route)
            else:
                setattr(route, change, [] if change == "knowledge_ids" else "New user value")
            await other.commit()
        assert await route_pipeline._save_route_result(snapshot, "Stale generated result", db) is None
        assert not db.in_transaction()
        async with small_pool() as other:
            saved = await other.get(ResearchRoute, "route")
            if change == "delete":
                assert saved is None
            else:
                assert getattr(saved, change) == ([] if change == "knowledge_ids" else "New user value")
                assert saved.ai_analysis != "Stale generated result"


@pytest.mark.asyncio
async def test_route_publish_commits_once_and_rejects_same_old_version(small_pool):
    async with small_pool() as db:
        snapshot = (await route_pipeline._resolve_route_context("route", db))["route"]
        result = await route_pipeline._save_route_result(snapshot, "First result", db)
        assert result["ai_analysis"] == "First result"
        assert result["description"] == "Original requirement"
        assert not db.in_transaction()
        assert await route_pipeline._save_route_result(snapshot, "Second result", db) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_route_commit_failure_rolls_back_before_return(small_pool, monkeypatch, failure):
    async with small_pool() as db:
        snapshot = (await route_pipeline._resolve_route_context("route", db))["route"]
        monkeypatch.setattr(db, "commit", AsyncMock(side_effect=failure("synthetic failure")))
        with pytest.raises(failure):
            await route_pipeline._save_route_result(snapshot, "Not saved", db)
        assert not db.in_transaction()
        async with small_pool() as other:
            assert (await other.get(ResearchRoute, "route")).ai_analysis is None


@pytest.mark.asyncio
async def test_concurrent_route_images_have_immutable_per_run_urls(small_pool, tmp_path, monkeypatch):
    from app import config
    from app.services.diagram import architecture_judge, planner, route_planner
    from app.services.llm import gateway

    both_started = asyncio.Event()
    started = 0

    async def model(**_kwargs):
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 2)
        return SimpleNamespace(content="Supported plan", profile={"provider": "fake", "model": "fake"},
                               fallback_used=False)

    monkeypatch.setattr(inference, "chat_with_fallback", model)
    monkeypatch.setattr(config, "get_model_for_task", lambda _: {
        "provider": "fake", "model": "fake", "api_key": "", "base_url": "",
    })
    monkeypatch.setattr(inference, "RoutedLLMGateway", lambda **_kwargs: SimpleNamespace(usage={}))
    monkeypatch.setattr(planner, "plan_modules_with_llm", AsyncMock(return_value={
        "layout": "pipeline", "modules": [{"name": "Evidence", "desc": "Verify"}],
    }))
    monkeypatch.setattr(route_planner, "build_roadmap_for_route", AsyncMock(return_value={
        "prompt": "roadmap", "plan": {"plan_source": "model", "stages": []},
    }))
    monkeypatch.setattr(architecture_judge, "judge_architecture", AsyncMock(return_value=None))
    paths = {}
    owners = []

    class ImageGateway:
        def __init__(self, **_kwargs):
            self.owner = f"run-{len(owners)}"
            owners.append(self.owner)

        def configure(self, **kwargs):
            pass

        async def generate_image(self, *, prompt, save_path):
            assert prompt
            path = Path(save_path)
            assert path not in paths, "A concurrent run tried to overwrite another image"
            paths[path] = self.owner
            path.write_text(self.owner, encoding="utf-8")  # Synthetic image fixture only.
            await asyncio.sleep(0)
            return {"status": "ok", "url": "https://example.invalid/unused.png"}

    monkeypatch.setattr(gateway, "LLMGateway", ImageGateway)
    old = tmp_path / "generated" / "route_diagrams" / "route.png"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("previous saved image", encoding="utf-8")

    async def run():
        async with small_pool() as db:
            events = [event async for event in route_pipeline.stream_route_analysis("route", db)]
            assert not db.in_transaction()
            return events

    runs = await asyncio.gather(run(), run())
    assert len(paths) == 4
    assert sorted(events[-1]["event"] for events in runs) == ["done", "error"]
    for events in runs:
        urls = [event["data"][key] for event in events for key in ("image_url", "roadmap_url")
                if key in event.get("data", {})]
        assert len(urls) == 2
        assert len({paths[tmp_path / url.lstrip("/")] for url in urls}) == 1
    winner = next(events[-1]["data"] for events in runs if events[-1]["event"] == "done")
    saved_urls = re.findall(r"!\[[^]]*\]\(([^)]+)\)", winner["ai_analysis"])
    assert len(saved_urls) == 2
    assert len({(tmp_path / url.lstrip("/")).read_text(encoding="utf-8") for url in saved_urls}) == 1
    assert old.read_text(encoding="utf-8") == "previous saved image"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["analysis", "recommendation", "route"])
async def test_offline_fallback_uses_plain_snapshot_after_read_rollback(small_pool, monkeypatch, endpoint):
    unavailable = AsyncMock(side_effect=inference.AllModelsUnavailableError([], {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    }))
    monkeypatch.setattr(knowledge, "chat_with_fallback", unavailable)
    monkeypatch.setattr(inference, "chat_with_fallback", unavailable)
    async with small_pool() as db:
        if endpoint == "route":
            stream = route_pipeline.stream_route_analysis("route", db)
            try:
                await anext(stream)
                content = (await anext(stream))["data"]["text_analysis"]
                assert "Original requirement" in content
            finally:
                await stream.aclose()
        elif endpoint == "analysis":
            result = await knowledge.ai_analyze_research(AIAnalyzeRequest(knowledge_ids=["note"]), db)
            assert not result.model_completed
            content = result.analysis
        else:
            result = await knowledge.recommend_papers(RecommendRequest(knowledge_ids=["note"]), db)
            assert not result.model_completed
            content = result.recommendations
        assert "Original note" in content
        assert not db.in_transaction()
        await _another_request_reads(small_pool)


@pytest.mark.asyncio
async def test_route_without_linked_knowledge_can_publish(small_pool):
    async with small_pool() as db:
        route = await db.get(ResearchRoute, "route")
        route.knowledge_ids = None
        await db.commit()
        snapshot = (await route_pipeline._resolve_route_context("route", db))["route"]
        result = await route_pipeline._save_route_result(snapshot, "Goal-only plan", db)
        assert result["knowledge_ids"] == []
        assert result["ai_analysis"] == "Goal-only plan"
