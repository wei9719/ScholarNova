"""Similar-paper API contract and actual SQLite persistence, no external services."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.api.v1 import recommendations as api
from app.models.paper import PaperEntity
from app.schemas.paper import Paper
from app.schemas.similar_papers import SimilarPaperItem, SimilarPapersResponse


async def seed(db):
    entity = PaperEntity(id="seed", title="DeepSeek food safety", abstract="Source abstract", source="openalex", keywords=["food safety"])
    db.add(entity)
    await db.commit()
    return entity


def response(mode="topic", **values):
    return SimilarPapersResponse(seed_paper_id="seed", mode=mode, scope="retrieved metadata only", **values)


def item(id="candidate", doi="10.1/candidate", **values):
    return SimilarPaperItem(paper=Paper(id=id, title="Qwen food safety", doi=doi, source="crossref", **values), reason="food safety overlap", matched_terms=["food", "safety"])


@pytest.fixture(autouse=True)
def external_mock(monkeypatch):
    mock = AsyncMock(return_value=response())
    monkeypatch.setattr(api, "recommend_similar", mock)
    return mock


async def test_api_calls_real_service_contract_after_ending_db_transaction(client, db_session, external_mock):
    await seed(db_session)

    async def service(paper, mode, limit, *, keywords):
        assert not db_session.in_transaction()
        assert paper.id == "seed" and paper.abstract == "Source abstract"
        assert keywords == ["food safety"]
        assert mode == "structure" and limit == 3
        return response(mode, items=[item(abstract="Actual returned abstract")])

    external_mock.side_effect = service
    result = await client.post("/api/v1/recommendations/similar", json={"paper_id": "seed", "mode": "structure", "limit": 3})
    assert result.status_code == 200
    assert result.json()["items"][0]["paper"]["id"] == "candidate"
    entity = await db_session.get(PaperEntity, "candidate")
    assert entity.abstract == "Actual returned abstract"
    assert entity.canonical_doi == "10.1/candidate"
    assert not db_session.new


async def test_reuses_existing_doi_id_and_enriches_missing_abstract(client, db_session, external_mock):
    await seed(db_session)
    db_session.add(PaperEntity(id="old-id", title="Existing paper title", source="openalex", doi="https://doi.org/10.1/CANDIDATE"))
    await db_session.commit()
    external_mock.return_value = response(items=[item(abstract="Real newly retrieved abstract")])
    result = await client.post("/api/v1/recommendations/similar", json={"paper_id": "seed"})
    assert result.status_code == 200
    assert result.json()["items"][0]["paper"]["id"] == "old-id"
    entities = (await db_session.execute(select(PaperEntity))).scalars().all()
    assert {entity.id for entity in entities} == {"seed", "old-id"}
    existing = await db_session.get(PaperEntity, "old-id")
    assert existing.title == "Existing paper title"
    assert existing.abstract == "Real newly retrieved abstract"


async def test_missing_seed_returns_404_without_source_calls(client, external_mock):
    result = await client.post("/api/v1/recommendations/similar", json={"paper_id": "missing"})
    assert result.status_code == 404
    external_mock.assert_not_called()


@pytest.mark.parametrize("payload", [
    {"paper_id": " "}, {"paper_id": "x", "mode": "fake"},
    {"paper_id": "x", "limit": 0}, {"paper_id": "x", "limit": 13},
    {"paper_id": "x" * 256},
])
async def test_invalid_request_rejected(client, external_mock, payload):
    result = await client.post("/api/v1/recommendations/similar", json=payload)
    assert result.status_code == 422
    external_mock.assert_not_called()


async def test_shared_search_capacity_busy_rejects_before_db_or_sources(client, external_mock):
    pool = client._transport.app.state.search_capacity
    leases = [pool.reserve() for _ in range(pool.limit + pool.queue_limit)]
    try:
        result = await client.post("/api/v1/recommendations/similar", json={"paper_id": "not-loaded"})
        assert result.status_code == 429
        assert result.headers["retry-after"] == "2"
        external_mock.assert_not_called()
    finally:
        for lease in leases:
            lease.release()
    assert pool.reserved == 0


async def test_persistence_failure_is_not_reported_as_actionable_success(client, db_session, external_mock, monkeypatch):
    from sqlalchemy.exc import OperationalError
    await seed(db_session)
    external_mock.return_value = response(items=[item()])
    monkeypatch.setattr(api, "_persist_similar", AsyncMock(side_effect=OperationalError("secret SQL", {}, Exception("private"))))
    result = await client.post("/api/v1/recommendations/similar", json={"paper_id": "seed"})
    assert result.status_code == 503
    assert "private" not in result.text and "secret SQL" not in result.text
    assert client._transport.app.state.search_capacity.reserved == 0


async def test_unique_doi_race_recovers_with_existing_id(client, db_session, external_mock, monkeypatch):
    from app.services import paper_persistence
    await seed(db_session)
    db_session.add(PaperEntity(
        id="concurrent-id", title="Original metadata", source="openalex",
        doi="10.1/candidate", canonical_doi="10.1/candidate",
    ))
    await db_session.commit()
    external_mock.return_value = response(items=[item()])
    actual_lookup = paper_persistence._existing_paper
    calls = 0

    async def missed_first_read(db, paper):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return await actual_lookup(db, paper)

    monkeypatch.setattr(paper_persistence, "_existing_paper", missed_first_read)
    result = await client.post("/api/v1/recommendations/similar", json={"paper_id": "seed"})
    assert result.status_code == 200
    assert result.json()["items"][0]["paper"]["id"] == "concurrent-id"
    assert calls == 2
    assert await db_session.get(PaperEntity, "candidate") is None


async def test_endpoint_real_pipeline_sources_mocked_and_detail_usable(client, db_session, monkeypatch):
    from app.schemas.query import DataSource
    from app.services import similar_papers as service

    await seed(db_session)
    source = AsyncMock()
    source.name, source.last_error = "openalex", None
    source.search.return_value = [Paper(
        id="live-shape", title="Qwen food safety classification", source="openalex",
        abstract="This is the source abstract, not generated text.", doi="10.1/new",
    )]
    monkeypatch.setattr(service, "_sources", lambda *args: {DataSource.OPENALEX: source})
    monkeypatch.setattr(api, "recommend_similar", service.recommend_similar)
    result = await client.post("/api/v1/recommendations/similar", json={"paper_id": "seed"})
    assert result.status_code == 200
    assert result.json()["statistics"]["returned_count"] == 1
    assert result.json()["statistics"]["term_sample_count"] == 1
    detail = await client.get("/api/v1/papers/live-shape")
    assert detail.status_code == 200
    assert detail.json()["abstract"] == source.search.return_value[0].abstract
    source.search.assert_awaited_once()
    source.close.assert_awaited_once()
