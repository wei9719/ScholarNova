"""Date filtering reaches upstream sources; dates are observations, not freshness promises."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import search
from app.models.paper import PaperEntity
from app.models.search_run import SearchRun
from app.schemas.paper import Paper
from app.schemas.query import Constraint, DataSource, SubQuery
from app.schemas.search import SearchRequest
from app.services.paper_persistence import persist_papers
from app.services.search import ranker as ranker_module
from app.services.search.constraint_verifier import ConstraintVerifier
from app.services.search.deduplicator import Deduplicator
from app.services.search.ranker import Ranker
from app.services.search.retriever import Retriever, RetrieveResult, SourceStatus, publication_bounds
from app.services.sources import semantic_scholar as s2_module
from app.services.sources.arxiv import ArxivSource
from app.services.sources.crossref import CrossRefSource
from app.services.sources.local_library import LocalLibrarySource
from app.services.sources.openalex import OpenAlexSource
from app.services.sources.semantic_scholar import SemanticScholarSource


@pytest.mark.parametrize("values", [
    {"date_from": "2026-13-01"}, {"date_to": "2026-02-30"},
    {"date_from": "2026"}, {"date_to": "20261013"}, {"date_to": ""},
    {"date_from": "2026-10-14", "date_to": "2026-10-13"},
])
def test_request_rejects_invalid_date_range(values):
    with pytest.raises(ValidationError):
        SearchRequest(query="traffic prediction", **values)


def test_date_constraints_retain_days_and_intersect_planned_years():
    request = SearchRequest(query="traffic", date_from="2024-02-29", date_to="2026-10-13")
    constraints = search._collect_constraints(request)
    assert [(c.key, c.value) for c in constraints] == [
        ("publication_date", "2024-02-29"), ("publication_date", "2026-10-13"),
    ]
    constraints.append(Constraint(key="year", operator="gte", value=2025))
    assert publication_bounds(constraints) == {"date_from": "2025-01-01", "date_to": "2026-10-13"}
    constraints.append(Constraint(key="year", operator="lte", value=2023))
    with pytest.raises(ValueError, match="冲突"):
        publication_bounds(constraints)


@pytest.mark.parametrize("source_class, expected", [
    (OpenAlexSource, {"filter": "from_publication_date:2026-01-01,to_publication_date:2026-10-13"}),
    (CrossRefSource, {"filter": "from-pub-date:2026-01-01,until-pub-date:2026-10-13"}),
    (SemanticScholarSource, {"publicationDateOrYear": "2026-01-01:2026-10-13"}),
    (ArxivSource, {"search_query": "(all:traffic) AND submittedDate:[202601010000 TO 202610132359]"}),
])
async def test_source_filters_are_in_real_upstream_parameters(source_class, expected, monkeypatch, tmp_path):
    monkeypatch.setattr(s2_module, "_S2_CACHE_DIR", tmp_path / "s2")
    monkeypatch.setattr(s2_module, "_S2_CACHE", {})
    monkeypatch.setattr(s2_module, "_S2_CACHE_LOCKS", {})
    source = source_class()
    response = MagicMock()
    response.json.return_value = {"results": [], "message": {"items": []}, "data": []}
    response.text = '<feed xmlns="http://www.w3.org/2005/Atom" />'
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(source, "_request_with_arxiv_limit" if source_class is ArxivSource else "_request_with_retry", request)
    await source.search("traffic", 5, date_from="2026-01-01", date_to="2026-10-13")
    params = request.await_args.kwargs["params"]
    assert all(params[key] == value for key, value in expected.items())
    assert source.last_error is None


async def test_s2_cache_is_separated_by_date_range(monkeypatch, tmp_path):
    monkeypatch.setattr(s2_module, "_S2_CACHE_DIR", tmp_path / "s2")
    monkeypatch.setattr(s2_module, "_S2_CACHE", {})
    monkeypatch.setattr(s2_module, "_S2_CACHE_LOCKS", {})
    source = SemanticScholarSource()
    response = MagicMock()
    response.json.return_value = {"data": []}
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(source, "_request_with_retry", request)
    await source.search("same query", 5)
    await source.search("same query", 5, date_to="2026-10-13")
    await source.search("same query", 5, date_to="2026-10-13")
    await source.search("same query", 5, date_to="2026-10-14")
    assert request.await_count == 3  # One bounded cached reuse; no cross-range stale result.


async def test_retriever_forwards_dates_but_keeps_unfiltered_legacy_signature():
    source = SimpleNamespace(name="freshness-test", last_error=None, search=AsyncMock(return_value=[]))
    retriever = Retriever(sources={DataSource.OPENALEX: source})
    queries = [SubQuery(source=DataSource.OPENALEX, query="traffic", rationale="test")]
    await retriever.retrieve(queries, max_results=5, date_from="2026-01-01", date_to="2026-10-13")
    source.search.assert_awaited_once_with("traffic", 5, date_from="2026-01-01", date_to="2026-10-13")
    source.search.reset_mock()
    await retriever.retrieve(queries, max_results=5)
    source.search.assert_awaited_once_with("traffic", 5)


def test_same_year_future_day_is_excluded_but_year_only_is_honest_approximation():
    papers = [
        Paper(id="today", title="Traffic", source="crossref", year=2026, publication_date=date(2026, 10, 13)),
        Paper(id="future-day", title="Traffic", source="crossref", year=2026, publication_date=date(2026, 10, 14)),
        Paper(id="future-year", title="Traffic", source="crossref", year=2027),
        Paper(id="year-only", title="Traffic", source="crossref", year=2026),
        Paper(id="unknown", title="Traffic", source="crossref"),
        Paper(id="old", title="Traffic", source="crossref", year=2025),
    ]
    constraints = search._collect_constraints(SearchRequest(query="Traffic", date_from="2026-01-01", date_to="2026-10-13"))
    assert [p.id for p in Ranker().filter_hard_constraints(papers, constraints)] == ["today", "year-only"]
    assert papers[3].publication_date is None


async def test_date_verification_never_invokes_model_for_missing_precision():
    model = SimpleNamespace(chat=AsyncMock())
    verifier = ConstraintVerifier(llm_gateway=model)
    result = await verifier.verify(Paper(id="a", title="Traffic", source="crossref", year=2026), [
        Constraint(key="publication_date", operator="lte", value="2026-10-13"),
    ])
    assert result[0]["verdict"] == "unknown"
    model.chat.assert_not_awaited()


def test_crossref_first_online_date_wins_over_future_print_and_registration():
    paper = CrossRefSource()._parse_paper({
        "DOI": "10.1/date", "title": ["Traffic"],
        "created": {"date-parts": [[2024, 1, 1]]},
        "published-print": {"date-parts": [[2027, 1, 1]]},
        "published-online": {"date-parts": [[2026, 10, 13]]},
    })
    assert paper.year == 2026 and paper.publication_date == date(2026, 10, 13)


@pytest.mark.parametrize("parts", [[2026], [2026, 10], [2026, 2, 30], []])
def test_crossref_partial_or_invalid_date_never_invents_a_day(parts):
    paper = CrossRefSource()._parse_paper({
        "DOI": "10.1/partial", "title": ["Traffic"],
        "published-online": {"date-parts": [parts]},
        "created": {"date-parts": [[2026, 3, 1]]},
    })
    assert paper.publication_date is None
    assert paper.year == (2026 if 0 < len(parts) < 3 else None)


def test_source_dates_survive_parsing_and_dedup():
    oa = OpenAlexSource()._parse_paper({"id": "https://openalex.org/W1", "title": "Traffic prediction", "publication_year": 2026, "publication_date": "2026-10-13"})
    arxiv = ArxivSource()._parse_atom_feed('''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>https://arxiv.org/abs/2610.00001</id><title>Traffic forecasting</title>
        <published>2026-10-01T13:00:00Z</published><updated>2026-10-13T13:00:00Z</updated>
        </entry></feed>''')[0]
    assert oa.publication_date == date(2026, 10, 13)
    assert arxiv.publication_date == date(2026, 10, 1)  # Updated version is not first submission.
    duplicate = Paper(id="another", title=oa.title, source="crossref", year=2026)
    assert Deduplicator()._merge_papers(duplicate, oa).publication_date == oa.publication_date


async def test_dates_saved_for_new_and_existing_records(db_session):
    db_session.add(PaperEntity(id="old", title="Traffic prediction", source="openalex", doi="10.1/old"))
    await db_session.commit()
    papers = [
        Paper(id="alias", title="Traffic prediction", source="crossref", doi="10.1/old", year=2026, publication_date=date(2026, 10, 13)),
        Paper(id="new", title="Traffic forecasting", source="crossref", year=2026, publication_date=date(2026, 10, 12)),
    ]
    result = await persist_papers(db_session, papers)
    await db_session.commit()
    assert [p.id for p in result] == ["old", "new"]
    assert (await db_session.get(PaperEntity, "old")).publication_date == date(2026, 10, 13)
    assert (await db_session.get(PaperEntity, "new")).publication_date == date(2026, 10, 12)


async def test_local_source_applies_dates_before_candidate_limit(test_engine):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            PaperEntity(id="today", title="Traffic prediction", source="zotero", year=2026, publication_date=date(2026, 10, 13)),
            PaperEntity(id="later", title="Traffic prediction", source="zotero", year=2026, publication_date=date(2026, 10, 14)),
            PaperEntity(id="coarse", title="Traffic prediction", source="zotero", year=2026),
            PaperEntity(id="future", title="Traffic prediction", source="zotero", year=2027),
        ])
        await db.commit()
    papers = await LocalLibrarySource(factory).search("Traffic", 5, date_to="2026-10-13")
    assert {p.id for p in papers} == {"today", "coarse"}


def test_ranker_calendar_rolls_forward_without_code_change(monkeypatch):
    class NextYear(date):
        @classmethod
        def today(cls):
            return cls(2027, 1, 1)

    monkeypatch.setattr(ranker_module, "date", NextYear)
    last_year = Paper(id="old", title="Traffic", source="crossref", year=2026, citation_count=10)
    this_year = Paper(id="new", title="Traffic", source="crossref", year=2027, citation_count=10)
    old_uncited = Paper(id="uncited", title="Traffic", source="crossref", year=2024)
    future = Paper(id="future", title="Traffic", source="crossref", year=2028)
    Ranker._attach_quality_signals([last_year, this_year, old_uncited, future], [last_year, this_year, old_uncited, future])
    assert last_year.quality.citation_velocity == 5
    assert this_year.quality.citation_velocity == 10
    assert old_uncited.quality.impact_label == "limited_signal"
    assert future.quality.impact_label == "limited_signal"
    assert Ranker()._calculate_timeliness_score(last_year) < Ranker()._calculate_timeliness_score(this_year)


async def test_search_worker_forwards_range_persists_dates_and_reports_scope(test_engine, monkeypatch):
    from app import database
    from app.core import cache
    from app.services import inference

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as db:
        db.add(SearchRun(id="fresh", raw_query="traffic prediction", status="pending"))
        await db.commit()
    papers = [
        Paper(id="today", title="Traffic prediction", source="crossref", year=2026, publication_date=date(2026, 10, 13)),
        Paper(id="later", title="Traffic prediction tomorrow", source="crossref", year=2026, publication_date=date(2026, 10, 14)),
        Paper(id="coarse", title="Urban traffic forecasting", source="crossref", year=2026),
    ]
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(inference, "RoutedLLMGateway", lambda **kwargs: SimpleNamespace(usage={"total_tokens": 0}))
    retrieve = AsyncMock(return_value=RetrieveResult(papers=papers, source_statuses=[SourceStatus(source="crossref", success=True, paper_count=3)]))
    monkeypatch.setattr(Retriever, "retrieve", retrieve)
    saved = {}

    async def cache_set(key, value, ttl):
        saved[key] = value

    monkeypatch.setattr(cache, "CacheManager", lambda: SimpleNamespace(set=cache_set, get=AsyncMock(side_effect=lambda key: saved.get(key))))
    await search._execute_search_task("fresh", SearchRequest(
        query="traffic prediction", sources=[DataSource.CROSSREF], planning_mode="rules",
        date_from="2026-01-01", date_to="2026-10-13", preferences={"iterative_search": False},
    ))
    assert retrieve.await_args.kwargs["date_to"] == "2026-10-13"
    async with factory() as db:
        detail = await search.get_search_run("fresh", db)
        assert detail.status == "completed"
        assert {p.id for p in detail.results} == {"today", "coarse"}
        assert (await db.get(PaperEntity, "today")).publication_date == date(2026, 10, 13)
        assert detail.runtime_metrics["date_to"] == "2026-10-13"
        assert detail.runtime_metrics["searched_at"].endswith("+00:00")
        assert detail.runtime_metrics["year_only_count"] == 1
        assert "不能保证当日完整" in detail.runtime_metrics["freshness_note"]
