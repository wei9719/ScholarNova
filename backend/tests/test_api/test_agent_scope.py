"""Real SQLite scope filtering; models and Zotero are strictly offline fakes."""

from datetime import datetime
import re
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import agent
from app.database import Base, get_db
from app.models.knowledge import KnowledgeBase, KnowledgeChunk
from app.models.paper import PaperChunk, PaperEntity
from app.services.inference.model_router import RoutedChatResult


FOOD_IDS = {"food-1", "food-duplicate", "food-unlinked", "food-missing", "food-no-pdf"}


def pdf_chunk(paper_id, position, marker, created_at=None):
    content = f"Evidence verification {marker} requires checking source material."
    return PaperChunk(
        id=f"{paper_id}-{position}", paper_id=paper_id, position=position,
        kind="fulltext", heading="Evidence verification", page=position + 1,
        content=content, content_hash=f"hash-{paper_id}-{position}",
        feature_version="test-v1", char_count=len(content),
        created_at=created_at or datetime(2020, 1, 1),
    )


@pytest_asyncio.fixture
async def scope_env(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'agent-scope.sqlite').as_posix()}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sessions() as db:
        for paper_id, marker in (
            ("food-paper", "FOOD_ONLY"), ("traffic-paper", "TRAFFIC_ONLY"),
            ("unlinked-paper", "UNLINKED_ONLY"), ("no-pdf-paper", "NO_PDF"),
        ):
            db.add(PaperEntity(id=paper_id, title=f"Evidence verification {marker}", source="test"))
        await db.flush()
        db.add_all([
            pdf_chunk("food-paper", 0, "FOOD_ONLY"),
            pdf_chunk("food-paper", 1, "FOOD_ONLY"),
            pdf_chunk("traffic-paper", 0, "TRAFFIC_ONLY"),
            pdf_chunk("unlinked-paper", 0, "UNLINKED_ONLY"),
        ])
        for kid, category, source, marker in (
            ("food-1", "食品", "food-paper", "FOOD_ONLY"),
            ("food-duplicate", "食品", "food-paper", "FOOD_ONLY"),
            ("food-unlinked", "食品", None, "FOOD_ONLY"),
            ("food-missing", "食品", "missing-paper", "FOOD_ONLY"),
            ("food-no-pdf", "食品", "no-pdf-paper", "FOOD_ONLY"),
            ("traffic-1", "交通", "traffic-paper", "TRAFFIC_ONLY"),
        ):
            db.add(KnowledgeBase(
                id=kid, title=f"Evidence verification {marker}", category=category,
                source_paper_id=source,
                content=f"Evidence verification {marker} requires checking source material.",
                updated_at=datetime(2020, 1, 1),
            ))
        await db.commit()

    active_sessions = []
    captured_messages = []

    async def request_db():
        async with sessions() as db:
            active_sessions.append(db)
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def fake_model(**kwargs):
        # Scoped lazy feature backfills must be committed before cloud waiting.
        assert not active_sessions[-1].in_transaction()
        captured_messages.append(kwargs["messages"])
        return RoutedChatResult(
            content="Evidence verification requires checking source material. [S1]",
            profile={"provider": "custom", "model": "offline-scope-test"},
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20, "requests": 1},
            attempts=(), fallback_used=False,
        )

    model = AsyncMock(side_effect=fake_model)
    zotero = AsyncMock(return_value=[{
        "key": "zotero-test", "data": {
            "title": "Evidence verification ZOTERO_ONLY",
            "abstractNote": "Evidence verification ZOTERO_ONLY requires checking source material.",
        },
    }])
    monkeypatch.setattr(agent, "check_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "chat_with_fallback", model)
    monkeypatch.setattr(agent.ZoteroLocalClient, "search_items", zotero)
    monkeypatch.setattr(agent, "get_model_for_task", lambda _task: {
        "provider": "custom", "model": "offline-scope-test",
    })
    monkeypatch.setattr("app.services.retrieval.hybrid.get_embedding_config", lambda: {"enabled": False})
    app = FastAPI()
    app.include_router(agent.router, prefix="/api/v1/agent")
    app.dependency_overrides[get_db] = request_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions, model, zotero, captured_messages
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_category_limits_knowledge_and_linked_pdf_without_duplicate_join(scope_env):
    _, sessions, _, _, _ = scope_env
    async with sessions() as db:
        knowledge = await agent._knowledge_candidates(db, "食品")
        papers = await agent._paper_candidates(db, "食品")
        assert {item.metadata["knowledge_id"] for item in knowledge} == FOOD_IDS
        assert {item.metadata["category"] for item in knowledge} == {"食品"}
        # Two notes link one paper, but its two chunks appear exactly once each.
        assert [item.chunk_id for item in papers] == ["food-paper-0", "food-paper-1"]
        assert {item.metadata["paper_id"] for item in papers} == {"food-paper"}
        indexed = set((await db.scalars(select(KnowledgeChunk.knowledge_id))).all())
        assert indexed == FOOD_IDS


@pytest.mark.asyncio
async def test_category_filter_happens_before_candidate_limits(scope_env):
    _, sessions, _, _, _ = scope_env
    async with sessions() as db:
        db.add_all(KnowledgeBase(
            id=f"traffic-new-{index}", title="Evidence verification TRAFFIC_ONLY", category="交通",
            content="Evidence verification TRAFFIC_ONLY", updated_at=datetime(2026, 1, 1),
        ) for index in range(81))
        db.add_all(pdf_chunk("traffic-paper", index, "TRAFFIC_ONLY", datetime(2026, 1, 1))
                   for index in range(1, 602))
        await db.commit()
        knowledge = await agent._knowledge_candidates(db, "食品")
        papers = await agent._paper_candidates(db, "食品")
        assert {item.metadata["knowledge_id"] for item in knowledge} == FOOD_IDS
        assert len(papers) == 2 and all(item.metadata["paper_id"] == "food-paper" for item in papers)


@pytest.mark.asyncio
@pytest.mark.parametrize("category,allowed,excluded", [
    ("食品", "FOOD_ONLY", "TRAFFIC_ONLY"), ("交通", "TRAFFIC_ONLY", "FOOD_ONLY"),
])
@pytest.mark.parametrize("use_zotero", [False, True])
async def test_model_receives_only_selected_category(scope_env, category, allowed, excluded, use_zotero):
    client, _, model, zotero, messages = scope_env
    response = await client.post("/api/v1/agent/chat", json={
        "question": "What does evidence verification require?",
        "knowledge_category": category, "use_zotero": use_zotero,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["inference_mode"] == "model" and body["citations"]
    assert all(allowed in item["title"] for item in body["citations"])
    prompt = "\n".join(message["content"] for message in messages[0])
    assert allowed in prompt
    assert excluded not in prompt and "UNLINKED_ONLY" not in prompt and "ZOTERO_ONLY" not in prompt
    zotero_step = next(step for step in body["tool_steps"] if step["tool"] == "zotero_search")
    assert zotero_step["status"] == "skipped"
    assert "当前资料分类不包含Zotero" in zotero_step["detail"]
    zotero.assert_not_awaited()
    model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["不存在的分类", "食品' OR 1=1 --"])
async def test_unknown_category_never_expands_to_all_sources(scope_env, category):
    client, sessions, model, zotero, _ = scope_env
    response = await client.post("/api/v1/agent/chat", json={
        "question": "What does evidence verification require?", "knowledge_category": category,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == [] and body["inference_mode"] == "none"
    assert "不会改查全库或 Zotero" in body["answer"]
    model.assert_not_awaited()
    zotero.assert_not_awaited()
    async with sessions() as db:
        assert (await db.scalars(select(KnowledgeChunk.id))).all() == []


@pytest.mark.asyncio
async def test_null_scope_preserves_all_library_and_zotero_behavior(scope_env):
    client, sessions, model, zotero, _ = scope_env
    async with sessions() as db:
        knowledge = await agent._knowledge_candidates(db)
        papers = await agent._paper_candidates(db)
        assert {item.metadata["category"] for item in knowledge} == {"食品", "交通"}
        assert {item.metadata["paper_id"] for item in papers} == {
            "food-paper", "traffic-paper", "unlinked-paper",
        }
        await db.commit()
    response = await client.post("/api/v1/agent/chat", json={
        "question": "What does evidence verification require?", "knowledge_category": None,
    })
    assert response.status_code == 200
    step = next(step for step in response.json()["tool_steps"] if step["tool"] == "zotero_search")
    assert step["status"] == "completed"
    model.assert_awaited_once()
    zotero.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_knowledge_never_reads_local_material(scope_env, monkeypatch):
    client, _, model, zotero, _ = scope_env
    knowledge = AsyncMock(side_effect=AssertionError("Knowledge lookup is disabled"))
    papers = AsyncMock(side_effect=AssertionError("PDF lookup is disabled"))
    monkeypatch.setattr(agent, "_knowledge_candidates", knowledge)
    monkeypatch.setattr(agent, "_paper_candidates", papers)
    response = await client.post("/api/v1/agent/chat", json={
        "question": "What does evidence verification require?", "knowledge_category": "食品",
        "use_knowledge": False, "use_zotero": True,
    })
    assert response.status_code == 200
    assert response.json()["citations"] == []
    assert "尚未启用知识库检索" in response.json()["answer"]
    knowledge.assert_not_awaited()
    papers.assert_not_awaited()
    model.assert_not_awaited()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["", " \t\n ", "分" * 101])
async def test_invalid_category_is_422_not_all_library(scope_env, category):
    client, _, model, zotero, _ = scope_env
    response = await client.post("/api/v1/agent/chat", json={
        "question": "What does evidence verification require?", "knowledge_category": category,
    })
    assert response.status_code == 422
    model.assert_not_awaited()
    zotero.assert_not_awaited()


def test_category_schema_preserves_legacy_default_and_accepts_maximum_length():
    assert agent.AgentChatRequest(question="Test question").knowledge_category is None
    assert agent.AgentChatRequest(question="Test question", knowledge_category="分" * 100).knowledge_category == "分" * 100
    assert agent.AgentChatRequest(question="Test question", knowledge_category=" 食品 ").knowledge_category == " 食品 "


@pytest.mark.asyncio
async def test_category_with_trailing_space_is_a_distinct_sqlite_scope(scope_env):
    client, sessions, model, zotero, _ = scope_env
    async with sessions() as db:
        db.add(PaperEntity(id="space-paper", title="Evidence verification SPACE_ONLY", source="test"))
        await db.flush()
        db.add(pdf_chunk("space-paper", 0, "SPACE_ONLY"))
        db.add(KnowledgeBase(
            id="space-note", title="Evidence verification SPACE_ONLY", category="食品 ",
            source_paper_id="space-paper", content="Evidence verification SPACE_ONLY requires checking source material.",
        ))
        await db.commit()

        ordinary = await agent._knowledge_candidates(db, "食品")
        exact = await agent._knowledge_candidates(db, "食品 ")
        assert {item.metadata["knowledge_id"] for item in ordinary} == FOOD_IDS
        assert {item.metadata["knowledge_id"] for item in exact} == {"space-note"}
        assert {item.metadata["paper_id"] for item in await agent._paper_candidates(db, "食品")} == {"food-paper"}
        assert {item.metadata["paper_id"] for item in await agent._paper_candidates(db, "食品 ")} == {"space-paper"}
        await db.commit()

    response = await client.post("/api/v1/agent/chat", json={
        "question": "What does evidence verification require?", "knowledge_category": "食品 ",
    })
    assert response.status_code == 200
    assert response.json()["citations"]
    assert all("SPACE_ONLY" in item["title"] for item in response.json()["citations"])
    model.assert_awaited_once()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["custom", "local"])
async def test_history_citations_are_not_reused_for_changed_current_evidence(scope_env, monkeypatch, provider):
    client, sessions, model, _, messages = scope_env
    monkeypatch.setattr(agent, "get_model_for_task", lambda _task: {
        "provider": provider, "model": "offline-scope-test",
    })
    payload = {
        "question": "What does evidence verification require?", "knowledge_category": "食品",
    }
    first = await client.post("/api/v1/agent/chat", json=payload)
    assert first.status_code == 200
    old_answer = first.json()["answer"] + " [s1]"
    assert first.json()["citations"][0]["id"] == "S1"
    assert "FOOD_ONLY" in first.json()["citations"][0]["title"]

    # The same scope can contain updated sources between two conversation turns.
    async with sessions() as db:
        for item in (await db.scalars(select(KnowledgeBase).where(KnowledgeBase.category == "食品"))).all():
            item.title = item.title.replace("FOOD_ONLY", "UPDATED_ONLY")
            item.content = item.content.replace("FOOD_ONLY", "UPDATED_ONLY")
        paper = await db.get(PaperEntity, "food-paper")
        paper.title = paper.title.replace("FOOD_ONLY", "UPDATED_ONLY")
        for chunk in (await db.scalars(select(PaperChunk).where(PaperChunk.paper_id == "food-paper"))).all():
            chunk.content = chunk.content.replace("FOOD_ONLY", "UPDATED_ONLY")
        await db.commit()

    history = [
        {"role": "user", "content": payload["question"]},
        {"role": "assistant", "content": old_answer},
    ]
    second = await client.post("/api/v1/agent/chat", json={**payload, "history": history})
    assert second.status_code == 200
    assert model.await_count == 2
    current = second.json()
    assert current["inference_mode"] == "model" and "[S1]" in current["answer"]
    assert current["citations"][0]["id"] == "S1"
    assert "UPDATED_ONLY" in current["citations"][0]["title"]
    model_history = next(message["content"] for message in messages[-1] if message["role"] == "assistant")
    assert "历史引用 S1，非本轮来源" in model_history
    assert re.findall(r"\[s\d+\]", model_history, re.IGNORECASE) == []
    assert "历史对话仅用于理解追问" in messages[-1][0]["content"]
    assert "不得继承上轮编号" in messages[-1][0]["content"]
    assert "[S1]" in messages[-1][-1]["content"]
    assert "UPDATED_ONLY" in messages[-1][-1]["content"]
    assert "FOOD_ONLY" not in messages[-1][-1]["content"]
    assert history[-1]["content"] == old_answer
