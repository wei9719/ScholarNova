"""Offline recommendation evidence, bounds and cancellation regression tests."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.schemas.paper import Paper
from app.schemas.query import DataSource
from app.services import similar_papers as service


def paper(id, title="DeepSeek food safety classification", **values):
    return Paper(id=id, title=title, source="openalex", **values)


class Source:
    def __init__(self, name, papers=(), error=None):
        self.name, self.papers, self.last_error = name, list(papers), error
        self.closed = False
        self.calls = []

    async def search(self, query, max_results):
        self.calls.append((query, max_results))
        return self.papers

    async def close(self):
        self.closed = True


@pytest.fixture
def sources(monkeypatch):
    mapping = {DataSource.OPENALEX: Source("openalex"), DataSource.CROSSREF: Source("crossref")}
    monkeypatch.setattr(service, "_sources", lambda seed, mode: mapping)
    return mapping


async def test_topic_uses_real_records_excludes_seed_and_deduplicates(sources):
    seed = paper("seed", doi="10.1/seed")
    sources[DataSource.OPENALEX].papers = [
        paper("seed-other-id", doi="https://doi.org/10.1/SEED"),
        paper("title-copy", title=seed.title.upper()),
        paper("food", title="Qwen food safety detection", doi="10.2/food", abstract="Original food safety abstract."),
        paper("unrelated", title="GPT for poetry translation", abstract="Poetry only."),
    ]
    sources[DataSource.CROSSREF].papers = [
        paper("food-copy", title="Qwen food safety detection", doi="https://doi.org/10.2/food"),
        paper("other", title="GPT food classification", abstract="Another original abstract."),
    ]
    result = await service.recommend_similar(seed, "topic", 6)
    assert {str(item.paper.id) for item in result.items} == {"food", "other"}
    assert result.items[0].paper.abstract == "Original food safety abstract."
    assert result.statistics.candidate_count == 3
    assert result.statistics.term_sample_count == 3
    assert result.statistics.returned_count == 2
    assert next(term.count for term in result.statistics.terms if term.term == "food") == 2
    counts = {term.term: term.count for term in result.statistics.terms}
    assert counts["DeepSeek"] == 0 and counts["Qwen"] == 1 and counts["GPT"] == 2
    assert result.statistics.terms[0].term == "DeepSeek"
    assert all(source.closed and len(source.calls) == 1 for source in sources.values())
    assert all(source.calls[0][1] == 30 for source in sources.values())
    assert "large language model" in sources[DataSource.OPENALEX].calls[0][0]
    assert "全文" in result.scope and "不调用生成模型" in result.scope


async def test_journal_exact_unicode_venue_and_sample_denominator(sources):
    seed = paper("seed", venue=" 食品科学 ")
    sources[DataSource.OPENALEX].papers = [
        paper("food", title="Qwen food safety detection", venue="食品科学", abstract="food food food"),
        paper("food-two", title="Food safety benchmarks", venue="食品科学"),
        paper("unrelated", title="Fruit transport logistics", venue="食品科学"),
        paper("wrong", title="Food safety algorithms", venue="食品科学与工程"),
        paper("other", title="Food analysis", venue="交通科学"),
    ]
    result = await service.recommend_similar(seed, "journal", 1)
    assert len(result.items) == 1
    assert result.statistics.candidate_count == 5
    assert result.statistics.same_venue_count == 3
    assert result.statistics.matched_count == 2
    assert result.statistics.term_sample_count == 3
    assert result.statistics.term_sample_scope == "same_venue"
    assert next(term.count for term in result.statistics.terms if term.term == "food") == 2
    assert all(item.paper.venue == "食品科学" for item in result.items)


async def test_structure_requires_abstract_and_observable_shared_clues(sources):
    seed = paper("seed", abstract="Background: Food safety is difficult. Methods: We propose a framework. Results: Experiments improve accuracy. Conclusion: Findings suggest utility.")
    sources[DataSource.OPENALEX].papers = [
        paper("match", title="Food safety with Qwen", abstract="Methods: We propose food detection. Results: Experiments show accuracy."),
        paper("no-abstract", title="Food safety experiment results"),
        paper("single", title="Food safety benchmarks", abstract="Background: Food safety."),
        paper("wrong-topic", title="Ocean currents", abstract="Methods: We propose a framework. Results: Experiments show accuracy."),
    ]
    result = await service.recommend_similar(seed, "structure", 6)
    assert [item.paper.id for item in result.items] == ["match"]
    assert result.items[0].structure_labels == ["方法/方案", "实验/结果"]
    assert "不代表全文结构" in result.scope
    assert "仅供摘要写作参考" in result.items[0].reason


@pytest.mark.parametrize("mode,values", [("structure", {}), ("structure", {"abstract": "Background only."}), ("journal", {"venue": " "})])
async def test_missing_seed_evidence_returns_honest_empty_without_external_calls(sources, mode, values):
    result = await service.recommend_similar(paper("seed", **values), mode, 6)
    assert result.items == [] and result.warnings
    assert not any(source.calls for source in sources.values())


async def test_partial_rate_limit_is_reported_without_secret_error(sources):
    sources[DataSource.OPENALEX].last_error = "HTTP 429 key=private-secret"
    sources[DataSource.CROSSREF].papers = [paper("food", title="Qwen food safety")]
    result = await service.recommend_similar(paper("seed"), "topic", 6)
    assert len(result.items) == 1 and result.warnings
    assert not result.source_statuses[0].success
    assert "限流" in result.source_statuses[0].error
    assert "private-secret" not in result.model_dump_json()


async def test_empty_is_not_claimed_as_no_existing_research(sources):
    result = await service.recommend_similar(paper("seed"), "topic", 6)
    assert not result.items
    assert result.statistics.candidate_count == 0
    assert all(status.success for status in result.source_statuses)
    assert "不代表" in result.warnings[-1]


async def test_sources_run_at_most_two_concurrently_and_cancel_closes_both(sources):
    entered = 0
    started = asyncio.Event()
    cancelled = []

    async def block(query, max_results):
        nonlocal entered
        entered += 1
        if entered == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    for source in sources.values():
        source.search = block
    task = asyncio.create_task(service.recommend_similar(paper("seed"), "topic", 6))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert entered == 2 and len(cancelled) == 2
    assert all(source.closed for source in sources.values())


async def test_source_timeout_preserves_completed_results(sources, monkeypatch):
    monkeypatch.setattr(service, "SOURCE_TIMEOUT", 0.01)
    async def block(*args):
        await asyncio.Event().wait()

    sources[DataSource.OPENALEX].search = block
    sources[DataSource.CROSSREF].papers = [paper("food", title="Qwen food safety")]
    result = await service.recommend_similar(paper("seed"), "topic", 6)
    assert len(result.items) == 1
    assert any("超时" in (status.error or "") for status in result.source_statuses)
    assert all(source.closed for source in sources.values())


async def test_journal_crossref_uses_container_field_query(monkeypatch):
    source = service._JournalCrossRefSource("Food Science", max_retries=0)
    request = AsyncMock(return_value=httpx.Response(200, json={"message": {"items": [{
        "DOI": "10.1/test", "title": ["Food safety"], "container-title": ["Food Science"],
    }]}}))
    monkeypatch.setattr(source, "_request_with_retry", request)
    papers = await source.search("large language model food", 30)
    assert len(papers) == 1
    params = request.call_args.kwargs["params"]
    assert params["query.container-title"] == "Food Science"
    assert params["query.bibliographic"] == "large language model food"
    assert params["rows"] == 30


def test_source_factory_has_no_retry_and_finite_timeout():
    sources = service._sources(paper("seed", venue="Food Science"), "journal")
    assert len(sources) == 2
    assert all(source.timeout == 12 and source.max_retries == 0 for source in sources.values())


def test_english_journal_normalization_does_not_fuzzy_match():
    assert service.normalize_venue(" Food   Science ") == service.normalize_venue("FOOD SCIENCE")
    assert service.normalize_venue("Food Science") != service.normalize_venue("Food Sciences")


async def test_generic_data_learning_system_and_shared_prediction_are_not_enough(sources):
    seed = paper("seed", title="DeepSeek deep learning system for traffic prediction and data repair")
    sources[DataSource.OPENALEX].papers = [
        paper("steel", title="Deep learning data system for steel structure prediction"),
        paper("food", title="Qwen system for food safety prediction"),
        paper("traffic", title="GPT traffic flow forecasting"),
    ]
    result = await service.recommend_similar(seed, "topic", 6)
    assert [item.paper.id for item in result.items] == ["traffic"]
    assert "traffic" in result.items[0].matched_terms
    assert "data" not in result.items[0].matched_terms


@pytest.mark.parametrize("task", ["repair", "imputation", "restoration", "completion"])
async def test_shared_generic_task_does_not_replace_research_domain_match(sources, task):
    seed = paper("seed", title=f"DeepSeek traffic flow data {task}")
    sources[DataSource.OPENALEX].papers = [
        paper("steel", title=f"GPT steel beam {task}"),
        paper("traffic", title=f"Qwen traffic flow {task}"),
    ]
    result = await service.recommend_similar(seed, "topic", 6)
    assert [item.paper.id for item in result.items] == ["traffic"]
    assert "traffic" in result.items[0].matched_terms
    assert result.statistics.candidate_count == 2


async def test_chinese_seed_retrieves_and_matches_english_research_terms(sources):
    seed = paper("seed", title="DeepSeek在交通流预测中的应用")
    sources[DataSource.OPENALEX].papers = [
        paper("traffic", title="Qwen for traffic flow prediction"),
        paper("steel", title="GPT learning systems for steel prediction"),
    ]
    result = await service.recommend_similar(seed, "topic", 6)
    assert [item.paper.id for item in result.items] == ["traffic"]
    query = sources[DataSource.OPENALEX].calls[0][0]
    assert "traffic flow prediction" in query
    assert "large language model" in query
    assert "DeepSeek" not in query
    assert "traffic" in result.items[0].matched_terms
    counts = {term.term: term.count for term in result.statistics.terms}
    assert counts["DeepSeek"] == 0 and counts["Qwen"] == 1 and counts["GPT"] == 1


async def test_brand_frequency_counts_documents_not_mentions_and_only_same_venue(sources):
    seed = paper("seed", venue="Food Science")
    sources[DataSource.OPENALEX].papers = [
        paper("food", title="DeepSeek food safety detection", venue="Food Science", abstract="DeepSeek DeepSeek Qwen Qwen GPT GPT"),
        paper("food2", title="Food benchmarks", venue="Food Science", abstract="Qwen Qwen"),
        paper("outside", title="Qwen food safety study", venue="Another Journal", abstract="DeepSeek GPT"),
    ]
    result = await service.recommend_similar(seed, "journal", 6)
    counts = {term.term: term.count for term in result.statistics.terms}
    assert result.statistics.term_sample_count == 2
    assert counts["DeepSeek"] == 1 and counts["Qwen"] == 2 and counts["GPT"] == 1


@pytest.mark.parametrize("mode", ["topic", "structure", "journal"])
async def test_explicit_llm_topic_requires_model_evidence_but_reference_modes_allow_other_methods(sources, mode):
    abstract = "Methods: We propose a food detection framework. Results: Experiments improve accuracy."
    seed = paper("seed", abstract=abstract, venue="Food Science")
    sources[DataSource.OPENALEX].papers = [
        paper("traditional", title="Food safety detection with conventional sensors", abstract=abstract, venue="Food Science"),
        paper("llm", title="Qwen food safety detection", abstract=abstract, venue="Food Science"),
    ]
    result = await service.recommend_similar(seed, mode, 6)
    assert {item.paper.id for item in result.items} == ({"llm"} if mode == "topic" else {"traditional", "llm"})
    assert result.statistics.term_sample_count == 2
    assert "模型线索" in result.scope if mode == "topic" else "其他方法" in result.scope
