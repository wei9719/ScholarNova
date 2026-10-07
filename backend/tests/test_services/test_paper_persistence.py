"""Cross-source identity reuse and search publish ordering using isolated SQLite."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import search
from app.models.paper import PaperEntity
from app.models.search_run import SearchRun
from app.schemas.paper import Paper
from app.schemas.query import DataSource
from app.schemas.search import SearchRequest
from app.services import paper_persistence
from app.services.search.retriever import RetrieveResult, SourceStatus


@pytest.mark.parametrize("identity", ["doi", "corpus"])
async def test_cross_source_identity_reuse_does_not_rollback_other_new_papers(db_session, identity):
    old = PaperEntity(
        id="stored-openalex", title="Existing traffic imputation", source="openalex",
        doi="https://doi.org/10.1038/OLD" if identity == "doi" else None,
        external_id="123456" if identity == "corpus" else None,
    )
    db_session.add(old)
    await db_session.commit()
    duplicate = Paper(
        id="crossref-id", title=old.title, source="crossref", abstract="Actual source abstract",
        doi="10.1038/old" if identity == "doi" else None,
        corpus_id="123456" if identity == "corpus" else None,
        relevance_score=0.71, ranking_score=0.65,
    )
    new = Paper(id="brand-new", title="New urban traffic forecasting", source="crossref", doi="10.2/new")
    result = await paper_persistence.persist_papers(db_session, [new, duplicate])
    await db_session.commit()
    assert [paper.id for paper in result] == ["brand-new", "stored-openalex"]
    assert result[1].relevance_score == 0.71 and result[1].ranking_score == 0.65
    assert (await db_session.get(PaperEntity, "brand-new")).canonical_doi == "10.2/new"
    assert await db_session.get(PaperEntity, "crossref-id") is None
    assert (await db_session.get(PaperEntity, "stored-openalex")).abstract == "Actual source abstract"


@pytest.mark.parametrize("fail_save", [False, True])
async def test_search_commits_local_ids_before_cache_and_never_completes_failed_save(test_engine, monkeypatch, fail_save):
    from app import database
    from app.core import cache
    from app.services import inference
    from app.services.search.retriever import Retriever

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as db:
        db.add(SearchRun(id="run", raw_query="traffic flow data repair", status="pending"))
        db.add(PaperEntity(id="stored-id", title="Traffic flow missing values imputation", doi="10.1/existing", source="openalex"))
        await db.commit()
    papers = [
        Paper(id="source-id", title="Traffic flow missing values imputation", doi="10.1/existing", source="crossref"),
        Paper(id="new-id", title="Urban vehicle congestion measurements repair", doi="10.2/new", source="crossref"),
    ]
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(inference, "RoutedLLMGateway", lambda **kwargs: SimpleNamespace(usage={"total_tokens": 0}))
    monkeypatch.setattr(Retriever, "retrieve", AsyncMock(return_value=RetrieveResult(
        papers=papers, source_statuses=[SourceStatus(source="crossref", success=True, paper_count=2)],
    )))
    published = []

    async def publish(key, value, ttl):
        assert key == "search_results:run"
        # A separate reader can resolve every ID before the result becomes visible.
        async with factory() as reader:
            assert (await reader.get(SearchRun, "run")).status == "running"
            for paper in value:
                assert await reader.get(PaperEntity, paper["id"]) is not None
        published.extend(value)

    save_cache = AsyncMock(side_effect=publish)
    monkeypatch.setattr(cache, "CacheManager", lambda: SimpleNamespace(set=save_cache))
    if fail_save:
        monkeypatch.setattr(paper_persistence, "persist_papers", AsyncMock(side_effect=OperationalError(
            "private SQL statement", {}, Exception("database unavailable"),
        )))
    await search._execute_search_task("run", SearchRequest(
        query="traffic flow data repair", sources=[DataSource.CROSSREF],
        planning_mode="rules", preferences={"iterative_search": False},
    ))
    async with factory() as reader:
        run = await reader.get(SearchRun, "run")
        if fail_save:
            assert run.status == "failed"
            assert "详情暂未能保存" in run.error_message
            assert "private SQL" not in run.error_message
            save_cache.assert_not_awaited()
        else:
            assert run.status == "completed"
            assert {paper["id"] for paper in published} == {"stored-id", "new-id"}
            assert await reader.get(PaperEntity, "source-id") is None
            assert len((await reader.execute(select(PaperEntity))).scalars().all()) == 2
            save_cache.assert_awaited_once()
