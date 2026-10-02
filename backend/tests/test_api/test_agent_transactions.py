"""File-backed SQLite checks for the agent's preparation transaction boundary."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import agent
from app.database import Base, get_db
from app.models.knowledge import KnowledgeBase, KnowledgeChunk
from app.services.inference.model_router import RoutedChatResult


@pytest_asyncio.fixture
async def transaction_env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-transactions.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sessions() as seed:
        seed.add(KnowledgeBase(
            id="source", title="Privacy evidence", category="Research",
            content="Privacy evidence requires citations for verification.",
        ))
        await seed.commit()

    requested_sessions = []
    clean_after_endpoint_error = []

    async def request_db():
        async with sessions() as session:
            requested_sessions.append(session)
            try:
                yield session
                # Exercise the production dependency's post-endpoint commit.
                await session.commit()
            except Exception:
                clean_after_endpoint_error.append(session.is_active and not session.in_transaction())
                await session.rollback()
                raise

    app = FastAPI()
    app.include_router(agent.router, prefix="/api/v1/agent")
    app.dependency_overrides[get_db] = request_db
    monkeypatch.setattr(agent, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "get_model_for_task", lambda _task: {
        "provider": "custom", "model": "test-model",
    })
    monkeypatch.setattr("app.services.retrieval.hybrid.get_embedding_config", lambda: {"enabled": False})
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            yield client, sessions, requested_sessions, clean_after_endpoint_error
    finally:
        await engine.dispose()


def _answer():
    return RoutedChatResult(
        content="Privacy evidence requires citations for verification. [S1]",
        profile={"provider": "custom", "model": "test-model"},
        usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30, "requests": 1},
        attempts=(), fallback_used=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_at", ["zotero", "model"])
async def test_feature_backfill_releases_writer_before_network_wait(transaction_env, monkeypatch, wait_at):
    client, sessions, requested_sessions, _ = transaction_env
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pause():
        assert not requested_sessions[0].in_transaction()
        entered.set()
        await release.wait()

    async def fake_zotero(_self, _query, **_kwargs):
        if wait_at == "zotero":
            await pause()
        return []

    async def fake_model(**_kwargs):
        if wait_at == "model":
            await pause()
        return _answer()

    monkeypatch.setattr(agent.ZoteroLocalClient, "search_items", fake_zotero)
    monkeypatch.setattr(agent, "chat_with_fallback", fake_model)
    request = asyncio.create_task(client.post("/api/v1/agent/chat", json={
        "question": "What does privacy evidence require?", "use_zotero": wait_at == "zotero",
    }))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        async with sessions() as writer:
            writer.add(KnowledgeBase(
                id="concurrent", title="Concurrent write", category="Research", content="New note",
            ))
            await asyncio.wait_for(writer.commit(), timeout=1)
            assert await writer.scalar(select(func.count()).select_from(KnowledgeChunk)) > 0
    finally:
        release.set()
    response = await request
    assert response.status_code == 200
    assert response.json()["inference_mode"] == "model"
    assert response.json()["total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["flush", "commit"])
async def test_preparation_failure_rolls_back_and_never_calls_remote_tools(transaction_env, monkeypatch, failure_at):
    client, sessions, _, clean_after_endpoint_error = transaction_env
    original = agent._paper_candidates

    async def fail_preparation(db):
        if failure_at == "flush":
            db.add(KnowledgeBase(id="source", title="Duplicate", category="Research", content="Duplicate"))
            await db.flush()
        else:
            monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
        return await original(db)

    remote_model = AsyncMock(side_effect=AssertionError("Preparation failure must not call a model"))
    remote_zotero = AsyncMock(side_effect=AssertionError("Preparation failure must not call Zotero"))
    monkeypatch.setattr(agent, "_paper_candidates", fail_preparation)
    monkeypatch.setattr(agent, "chat_with_fallback", remote_model)
    monkeypatch.setattr(agent.ZoteroLocalClient, "search_items", remote_zotero)
    response = await client.post("/api/v1/agent/chat", json={"question": "What does privacy evidence require?"})
    assert response.status_code == 500
    assert clean_after_endpoint_error == [True]
    remote_model.assert_not_awaited()
    remote_zotero.assert_not_awaited()
    async with sessions() as check:
        assert await check.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0
        check.add(KnowledgeBase(id="after-failure", title="Recovery", category="Research", content="Recovered"))
        await check.commit()
