"""Local study endpoint limits, no persistence, and connection lifecycle tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.responses import JSONResponse

from app.api.v1 import study as study_api
from app.core.capacity import ExpensiveRequestMiddleware, WorkPool
from app.database import Base
from app.models.knowledge import KnowledgeBase
from app.models.paper import PaperChunk, PaperEntity
from app.schemas.study import StudyDraftRequest
from app.services import study
from app.services.llm.gateway import LLMGateway


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    monkeypatch.setattr(study, "chat_with_fallback", AsyncMock(side_effect=AssertionError("Unexpected model call")))
    monkeypatch.setattr(LLMGateway, "chat", AsyncMock(side_effect=AssertionError("No real model in study tests")))
    monkeypatch.setattr(study_api, "check_rate_limit", lambda *_args, **_kwargs: None)


async def seed_paper(db, *, abstract="Local abstract.", chunks=0):
    db.add(PaperEntity(id="paper", title="Local paper", abstract=abstract, source="test"))
    await db.flush()
    for index in range(chunks):
        db.add(PaperChunk(
            id=f"chunk-{index}", paper_id="paper", position=index,
            kind="section", heading="Experiments", page=index + 1,
            content=(f"Local evidence {index}. " * 180), content_hash=str(index).zfill(64),
            feature_version="test-v1", char_count=100,
        ))
    await db.commit()


@pytest.mark.asyncio
async def test_registered_endpoint_resources_does_not_save_notes(client, db_session):
    await seed_paper(db_session, abstract="Code: https://example.org/code. author@example.org")
    response = await client.post("/api/v1/study/draft", json={"paper_id": "paper", "section": "resources"})
    assert response.status_code == 200
    body = response.json()
    assert body["section"] == "resources"
    assert body["material_scope"] == "abstract"
    assert body["model_completed"] is False
    assert body["sources"][0]["id"] == "abstract:paper"
    assert body["sources"][0]["page"] is None
    assert "https://example.org/code" in body["content"]
    assert await db_session.scalar(select(func.count()).select_from(KnowledgeBase)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("abstract", [None, "", "N/A", "  "])
async def test_missing_material_returns_422_without_model(client, db_session, abstract):
    await seed_paper(db_session, abstract=abstract)
    response = await client.post("/api/v1/study/draft", json={"paper_id": "paper", "section": "math"})
    assert response.status_code == 422
    assert "离线手写" in response.json()["detail"]
    study.chat_with_fallback.assert_not_called()
    assert not db_session.in_transaction()


@pytest.mark.asyncio
async def test_missing_paper_is_404(client):
    response = await client.post("/api/v1/study/draft", json={"paper_id": "missing", "section": "overview"})
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"paper_id": "p", "section": "all"},
    {"paper_id": "p", "section": "overview", "question": "x" * 2001},
    {"paper_id": "p", "section": ["overview", "figures"]},
])
async def test_request_limits_return_422(client, payload):
    assert (await client.post("/api/v1/study/draft", json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_material_snapshot_is_bounded_and_paper_scoped(db_session):
    await seed_paper(db_session, chunks=100)
    db_session.add(PaperEntity(id="other", title="Unrelated", abstract="PRIVATE OTHER ABSTRACT", source="test"))
    await db_session.flush()
    db_session.add(PaperChunk(id="other-chunk", paper_id="other", position=0, kind="section",
                             content="PRIVATE OTHER DATA", content_hash="b" * 64,
                             feature_version="v1", char_count=18))
    await db_session.commit()
    material = await study.load_study_material(db_session, StudyDraftRequest(paper_id="paper", section="experiments"))
    assert len(material.sources) == study.MAX_SOURCES
    assert material.scope == "fulltext_excerpt"
    assert all(len(source.excerpt) <= study.MAX_EXCERPT_CHARS for source in material.sources)
    assert all(source.id.startswith("chunk-") for source in material.sources)
    assert all("PRIVATE OTHER" not in source.excerpt for source in material.sources)
    assert material.sources[0].section == "Experiments"
    assert material.sources[0].page == 1
    assert not db_session.in_transaction()


@pytest.mark.asyncio
async def test_pdf_abstract_chunk_alone_does_not_claim_fulltext_coverage(db_session):
    await seed_paper(db_session, abstract=None)
    db_session.add(PaperChunk(
        id="parsed-abstract", paper_id="paper", position=0, kind="abstract",
        heading="Abstract", content="Only an abstract was indexed.", content_hash="c" * 64,
        feature_version="v1", char_count=29,
    ))
    await db_session.commit()
    material = await study.load_study_material(db_session, StudyDraftRequest(paper_id="paper", section="overview"))
    assert material.scope == "abstract"
    assert material.sources[0].id == "parsed-abstract"


@pytest.mark.asyncio
@pytest.mark.parametrize("expire", [False, True])
async def test_model_wait_releases_single_connection_and_writer(tmp_path, monkeypatch, expire):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'study.sqlite'}",
        pool_size=1, max_overflow=0, pool_timeout=0.1,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=expire)
    try:
        async with sessions() as db:
            await seed_paper(db)

            async def model(**_kwargs):
                assert not db.in_transaction()
                async with sessions() as other:
                    assert await other.scalar(select(PaperEntity.title).where(PaperEntity.id == "paper")) == "Local paper"
                    other.add(KnowledgeBase(id="user-note", title="Editable", category="Test", content="User thought"))
                    await other.commit()
                return SimpleNamespace(content="摘要内容 [S1]。", profile={"provider": "fake", "model": "mock"},
                                       usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}, fallback_used=False)

            monkeypatch.setattr(study, "chat_with_fallback", model)
            response = await study_api.create_study_draft(
                StudyDraftRequest(paper_id="paper", section="overview"), None, db,
            )
            assert response.model_completed
            assert not db.in_transaction()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_analysis_rate_limit_is_used(client, monkeypatch):
    def limited(_request, endpoint_type):
        assert endpoint_type == "analysis"
        return JSONResponse(status_code=429, content={"code": "RATE_LIMIT_ERROR"})

    monkeypatch.setattr(study_api, "check_rate_limit", limited)
    response = await client.post("/api/v1/study/draft", json={"paper_id": "paper", "section": "overview"})
    assert response.status_code == 429
    study.chat_with_fallback.assert_not_called()


@pytest.mark.asyncio
async def test_study_shares_expensive_work_capacity_and_rejects_before_work():
    pool = WorkPool(active=1, queued=0, wait_seconds=1)
    lease = pool.reserve()
    await lease.__aenter__()
    called = False

    async def upstream(_scope, _receive, _send):
        nonlocal called
        called = True

    app = ExpensiveRequestMiddleware(upstream, pool)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/study/draft", json={"paper_id": "paper", "section": "overview"})
            assert response.status_code == 429
            assert response.json()["code"] == "SERVICE_BUSY"
            assert not called
    finally:
        lease.release()
    assert pool.active == pool.reserved == 0
