from datetime import datetime, timezone
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.api.v1 import journals
from app.core.capacity import WorkPool
from app.models.paper import PaperEntity
from app.schemas.journals import JournalDiscoveryResponse, JournalPapersResponse
from app.schemas.paper import Paper


async def test_discover_rejects_blank_or_oversized_inputs_before_network(client, monkeypatch):
    discover = AsyncMock()
    monkeypatch.setattr(journals, "discover_journals", discover)
    for body in [{"query": " "}, {"query": "q" * 301}, {"query": "q", "candidates": [
        {"id": str(i), "title": "paper", "venue": "journal"} for i in range(101)
    ]}]:
        response = await client.post("/api/v1/journals/discover", json=body)
        assert response.status_code == 422
    discover.assert_not_awaited()


async def test_discover_returns_honest_empty_state(client, monkeypatch):
    monkeypatch.setattr(journals, "discover_journals", AsyncMock(return_value=JournalDiscoveryResponse(
        query="q", candidate_count=0, checked_at=datetime.now(timezone.utc), warnings=["源暂不可用"],
    )))
    response = await client.post("/api/v1/journals/discover", json={"query": "q"})
    assert response.status_code == 200 and response.json()["warnings"] == ["源暂不可用"]


async def test_journal_paper_ids_are_persisted_before_becoming_actionable(client, db_session, monkeypatch):
    monkeypatch.setattr(journals, "journal_papers", AsyncMock(return_value=JournalPapersResponse(
        source_id="S123", query="traffic", papers=[Paper(id="journal-test-paper", title="Traffic imputation",
            source="openalex", venue="Transport Research", doi="https://doi.org/10.1234/test", authors=["A. Author"])],
    )))
    response = await client.post("/api/v1/journals/S123/papers", json={"query": "traffic"})
    assert response.status_code == 200
    paper_id = response.json()["papers"][0]["id"]
    entity = (await db_session.execute(select(PaperEntity).where(PaperEntity.id == paper_id))).scalar_one()
    assert entity.title == "Traffic imputation" and entity.canonical_doi == "10.1234/test"
    detail = await client.get(f"/api/v1/papers/{paper_id}")
    assert detail.status_code == 200 and detail.json()["title"] == entity.title


async def test_journal_validation_does_not_call_remote_for_bad_id_or_dates(client, monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(journals, "journal_papers", lookup)
    response = await client.post("/api/v1/journals/W123/papers", json={"query": "q"})
    assert response.status_code == 422
    response = await client.post("/api/v1/journals/S123/papers", json={"query": "q", "date_from": "2026-12-01", "date_to": "2024-01-01"})
    assert response.status_code == 422
    lookup.assert_not_awaited()


async def test_nonjournal_source_is_rejected(client, monkeypatch):
    monkeypatch.setattr(journals, "journal_papers", AsyncMock(side_effect=journals.NotJournalError))
    response = await client.post("/api/v1/journals/S123/papers", json={"query": "q"})
    assert response.status_code == 422


async def test_discovery_uses_bounded_shared_search_admission(client, monkeypatch):
    pool = WorkPool(1, 0, .1)
    client._transport.app.state.search_capacity = pool
    discover = AsyncMock()
    monkeypatch.setattr(journals, "discover_journals", discover)
    async with pool.reserve():
        response = await client.post("/api/v1/journals/discover", json={"query": "q"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2" and pool.active == 0 and pool.reserved == 0
    discover.assert_not_awaited()
