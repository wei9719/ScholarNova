import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.journals import JournalDiscoveryRequest, JournalPapersRequest
from app.services import journal_quality, journals


def source(sid="S123", name="Transport Research", kind="journal", **extra):
    return {"id": f"https://openalex.org/{sid}", "display_name": name, "type": kind,
            "issn_l": "1234-567X", "issn": ["1234-567X"], **extra}


def work(sid="S123", wid="W12"):
    return {"id": f"https://openalex.org/{wid}", "title": "Traffic imputation", "publication_year": 2025,
            "primary_location": {"source": source(sid)}, "authorships": [], "cited_by_count": 2}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(journals, "_CACHE", {})
    monkeypatch.setattr(journal_quality, "_DATA_PATH", tmp_path / "journal_rankings.json")


async def test_discovers_real_journal_excludes_conference_and_preserves_sample_scope(monkeypatch):
    conference = work("S999", "W99")
    conference["primary_location"]["source"]["type"] = "conference"

    async def get(path, params):
        if path == "works":
            return {"results": [work(), conference]}
        if path == "sources/S123":
            return source(homepage_url="https://publisher.example/journal", summary_stats={"h_index": 98, "2yr_mean_citedness": 4.2})
        return {"results": []}

    monkeypatch.setattr(journals, "_get", AsyncMock(side_effect=get))
    request = JournalDiscoveryRequest(query="traffic imputation", candidates=[
        {"id": "p1", "title": "Missing speed", "venue": "Transport Research"},
        {"id": "p2", "title": "Conference paper", "venue": "Unverified Proceedings"},
    ])
    result = await journals.discover_journals(request)
    verified = [item for item in result.journals if item.verification == "verified_journal"]
    assert len(verified) == 1
    item = verified[0]
    assert item.source_id == "S123" and item.candidate_match_count == 1
    assert item.discovery_match_count == 1 and result.discovery_candidate_count == 2
    assert item.homepage_url and item.author_guidelines_url is None
    assert item.metrics[1].name == "OpenAlex 近两年篇均被引"
    assert item.metrics[1].year is None and "不是 JIF" in item.metrics[1].basis
    assert result.journals[1].verification == "pending"
    cached = await journals.discover_journals(request)
    assert cached.cached and cached is not result
    assert journals._get.await_count == 3


async def test_network_failure_keeps_current_venue_without_guessing_metrics(monkeypatch):
    monkeypatch.setattr(journals, "_get", AsyncMock(side_effect=TimeoutError))
    result = await journals.discover_journals(JournalDiscoveryRequest(query="q", candidates=[
        {"id": "p", "title": "paper", "venue": "Unknown Journal"},
    ]))
    assert len(result.journals) == 1 and result.journals[0].verification == "pending"
    assert result.journals[0].candidate_paper_ids == ["p"]
    assert not result.journals[0].metrics and not journals._CACHE
    assert any("未全部完成" in value for value in result.warnings)


async def test_source_name_matching_is_exact_not_fuzzy(monkeypatch):
    mock = AsyncMock(side_effect=[{"results": []}, {"results": [source(name="Nearly Same Journal")] }])
    monkeypatch.setattr(journals, "_get", mock)
    result = await journals.discover_journals(JournalDiscoveryRequest(query="q", candidates=[
        {"id": "p", "title": "paper", "venue": "Nearly Same Journal of Medicine"},
    ]))
    assert result.journals[0].verification == "pending"


async def test_deadline_covers_entire_discovery(monkeypatch):
    cancelled = asyncio.Event()

    async def blocked(*_args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(journals, "_get", blocked)
    monkeypatch.setattr(journals, "_BUDGET", .01)
    result = await asyncio.wait_for(journals.discover_journals(JournalDiscoveryRequest(query="q")), .2)
    assert cancelled.is_set() and result.warnings


async def test_journal_papers_filters_identity_and_applies_dates(monkeypatch):
    get = AsyncMock(side_effect=[source(), {"results": [work(), work("S999", "W99")]}])
    monkeypatch.setattr(journals, "_get", get)
    result = await journals.journal_papers("S123", JournalPapersRequest(query="traffic", date_from="2024-01-01", date_to="2026-10-16"))
    assert len(result.papers) == 1 and result.papers[0].title == "Traffic imputation"
    filters = get.call_args_list[1].args[1]["filter"]
    assert "primary_location.source.id:S123" in filters
    assert "from_publication_date:2024-01-01" in filters


async def test_journal_papers_rejects_nonjournal(monkeypatch):
    monkeypatch.setattr(journals, "_get", AsyncMock(return_value=source(kind="repository")))
    with pytest.raises(ValueError):
        await journals.journal_papers("S123", JournalPapersRequest(query="q"))


async def test_malformed_upstream_keeps_safe_empty_state(monkeypatch):
    monkeypatch.setattr(journals, "_get", AsyncMock(side_effect=[source(), {"results": "bad payload"}]))
    result = await journals.journal_papers("S123", JournalPapersRequest(query="q"))
    assert not result.papers and result.warnings
    monkeypatch.setattr(journals, "_get", AsyncMock(return_value={"results": [None, {"primary_location": "bad"}]}))
    result = await journals.discover_journals(JournalDiscoveryRequest(query="q"))
    assert not result.journals


@pytest.mark.parametrize("value", ["S1/works", "https://evil.example/S123", "S0", "W123", "S123?x=1"])
def test_source_id_cannot_change_request_path(value):
    assert journals.source_id(value) is None


@pytest.mark.parametrize("query", [" ", "q" * 301])
def test_query_limits(query):
    with pytest.raises(ValidationError):
        JournalDiscoveryRequest(query=query)


def test_jif_requires_year_and_retains_separate_provenance():
    journal_quality.import_ranking_content("Journal,JIF,JIF Year,Source\nTransport Research,5.4,2025,licensed JCR file\n", "jif.csv")
    values = journals._metrics(source(summary_stats={"2yr_mean_citedness": 7.2}))
    jif = next(value for value in values if value.name.startswith("JIF"))
    assert jif.value == 5.4 and jif.year == 2025 and jif.source == "licensed JCR file"
    assert journal_quality.local_journal_record("Transport Research", ["9999-9999"])
    assert not journal_quality.local_journal_record("Almost Transport Research")
    with pytest.raises(ValueError):
        journal_quality.import_ranking_content("Journal,JIF\nTransport Research,5.4\n", "bad.csv")


def test_conflicting_import_issn_does_not_apply():
    journal_quality.import_ranking_content("Journal,JIF,Year,ISSN\nTransport Research,5.4,2025,9999-9999\n", "jif.csv")
    assert not any(item.name.startswith("JIF") for item in journals._metrics(source()))
