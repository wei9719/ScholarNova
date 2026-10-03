"""
查询规划器测试
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.schemas.query import Constraint, DataSource
from app.services.search.query_planner import QueryPlanner


class TestQueryPlanner:
    """QueryPlanner 测试套件"""

    async def test_plan_returns_query_parse_result(self, mock_llm_gateway):
        """plan 应返回 QueryParseResult"""
        planner = QueryPlanner(llm_gateway=mock_llm_gateway)
        result = await planner.plan(
            query="transformer attention mechanism",
            sources=[DataSource.SEMANTIC_SCHOLAR],
        )
        assert result.original_query == "transformer attention mechanism"
        assert result.strategy is not None

    async def test_plan_generates_sub_queries_per_source(self, mock_llm_gateway):
        """应为每个数据源生成一个子查询"""
        planner = QueryPlanner(llm_gateway=mock_llm_gateway)
        sources = [DataSource.SEMANTIC_SCHOLAR, DataSource.OPENALEX, DataSource.CROSSREF]
        result = await planner.plan(
            query="deep learning",
            sources=sources,
        )
        assert len(result.sub_queries) == len(sources)

    async def test_plan_sub_queries_have_correct_source(self, mock_llm_gateway):
        """子查询应关联到正确的数据源"""
        planner = QueryPlanner(llm_gateway=mock_llm_gateway)
        sources = [DataSource.SEMANTIC_SCHOLAR, DataSource.OPENALEX]
        result = await planner.plan(query="NLP", sources=sources)

        result_sources = {sq.source for sq in result.sub_queries}
        expected_sources = set(sources)
        assert result_sources == expected_sources

    async def test_plan_sub_queries_have_rationale(self, mock_llm_gateway):
        """每个子查询应包含 rationale"""
        planner = QueryPlanner(llm_gateway=mock_llm_gateway)
        result = await planner.plan(
            query="test query",
            sources=[DataSource.SEMANTIC_SCHOLAR],
        )
        for sq in result.sub_queries:
            assert sq.rationale
            assert len(sq.rationale) > 0

    async def test_plan_extracts_keywords(self, mock_llm_gateway):
        """应提取关键词"""
        planner = QueryPlanner(llm_gateway=mock_llm_gateway)
        result = await planner.plan(
            query="attention mechanism in transformers",
            sources=[DataSource.SEMANTIC_SCHOLAR],
        )
        assert len(result.keywords) > 0

    async def test_plan_with_empty_sources(self, mock_llm_gateway):
        """空数据源列表应返回空子查询"""
        planner = QueryPlanner(llm_gateway=mock_llm_gateway)
        result = await planner.plan(
            query="test query",
            sources=[],
        )
        assert len(result.sub_queries) == 0

    async def test_plan_intent_is_set(self, mock_llm_gateway):
        """应设置查询意图"""
        planner = QueryPlanner(llm_gateway=mock_llm_gateway)
        result = await planner.plan(
            query="test query",
            sources=[DataSource.SEMANTIC_SCHOLAR],
        )
        assert result.intent is not None

    async def test_plan_without_llm_gateway(self):
        """没有 LLM 网关时应使用默认实现"""
        planner = QueryPlanner(llm_gateway=None)
        result = await planner.plan(
            query="test query",
            sources=[DataSource.SEMANTIC_SCHOLAR],
        )
        assert result.original_query == "test query"

    def test_build_prompt(self, mock_llm_gateway):
        """_build_prompt 应生成包含查询和数据源的 prompt"""
        planner = QueryPlanner(llm_gateway=mock_llm_gateway)
        prompt = planner._build_prompt(
            query="attention mechanism",
            sources=[DataSource.SEMANTIC_SCHOLAR, DataSource.OPENALEX],
        )
        assert "attention mechanism" in prompt
        assert "semantic_scholar" in prompt
        assert "openalex" in prompt

    @pytest.mark.parametrize(
        ("query", "canonical"),
        [
            (
                "the AlphaGeometry paper",
                "Solving olympiad geometry without human demonstrations",
            ),
            (
                "the gpt-2 paper",
                "Language Models are Unsupervised Multitask Learners",
            ),
            (
                "the cnn paper",
                "ImageNet classification with deep convolutional neural networks",
            ),
            (
                "the squad paper",
                "SQuAD: 100,000+ Questions for Machine Comprehension of Text",
            ),
        ],
    )
    def test_resolve_well_known_paper_aliases(self, query, canonical):
        assert QueryPlanner.resolve_paper_alias(query) == canonical

    @pytest.mark.parametrize(
        "query",
        [
            "BART by Lewis et al.",
            "the MS^2 DeYong2021 paper",
            "the paper about the Objaverse dataset",
            "SPIKE syntactic search paper",
            "the gpt-2 paper",
        ],
    )
    def test_confident_single_paper_lookup(self, query):
        assert QueryPlanner.is_confident_single_paper_lookup(query)

    def test_ambiguous_acronym_lookup_keeps_multiple_results(self):
        assert not QueryPlanner.is_confident_single_paper_lookup("the SPIKE paper")

    def test_bibkey_alias_expands_to_canonical_title(self):
        optimized = QueryPlanner._optimize_query_for_source(
            "the MS^2 DeYong2021 paper",
            ["MS2", "DeYong2021"],
            DataSource.SEMANTIC_SCHOLAR,
        )
        assert optimized == "MS2: Multi-Document Summarization of Medical Studies"

    async def test_complex_query_extracts_multidimensional_constraints(self):
        planner = QueryPlanner()
        result = await planner.plan(
            query="查找 2020-2024 年 ACL 使用 Transformer 在 PaperFindingBench 上的论文",
            sources=[DataSource.SEMANTIC_SCHOLAR, DataSource.OPENALEX],
        )

        assert "ACL" in {venue.upper() for venue in result.entities["venues"]}
        assert any("paperfindingbench" == item.lower() for item in result.entities["datasets"])
        assert any("transformer" == item.lower() for item in result.entities["methods"])
        assert {(item.operator, item.value) for item in result.constraints if item.key == "year"} == {
            ("gte", 2020),
            ("lte", 2024),
        }

    async def test_venue_alternatives_use_or_constraint(self):
        planner = QueryPlanner()
        result = await planner.plan(
            query="2020-2024 ACL 或 EMNLP 的文献检索论文",
            sources=[DataSource.OPENALEX],
        )
        venue = next(item for item in result.constraints if item.key == "venue")
        assert venue.operator == "in"
        assert {item.upper() for item in venue.value} == {"ACL", "EMNLP"}
        assert "literature search" in result.expanded_queries[0].lower()

    async def test_refinement_queries_are_bounded_per_source(self):
        planner = QueryPlanner()
        sources = [DataSource.SEMANTIC_SCHOLAR, DataSource.OPENALEX]
        result = await planner.plan(
            query="大语言模型在交通流预测中的使用",
            sources=sources,
        )
        refinements = planner.build_refinement_subqueries(result, sources)

        assert len(refinements) <= len(sources)
        assert {item.source for item in refinements}.issubset(set(sources))
        translated = planner._translate_to_english("大语言模型在交通流预测中的使用")
        assert "large language model" in translated
        assert "traffic flow prediction" in translated

    async def test_source_queries_keep_all_high_information_facets(self):
        planner = QueryPlanner()
        result = await planner.plan(
            query=(
                "visual question answering papers using Earth Mover's Distance "
                "(EMD) as an evaluation metric"
            ),
            sources=[DataSource.CROSSREF, DataSource.OPENALEX, DataSource.ARXIV],
        )
        for sub_query in result.sub_queries:
            normalized = sub_query.query.lower()
            assert "visual" in normalized
            assert "question" in normalized
            assert "earth" in normalized
            assert "mover" in normalized
            assert "emd" in normalized

    async def test_semantic_scholar_query_removes_hyphens(self):
        planner = QueryPlanner()
        result = await planner.plan(
            query="papers about data-efficient pre-training methods",
            sources=[DataSource.SEMANTIC_SCHOLAR],
        )
        assert "-" not in result.sub_queries[0].query

    async def test_author_citation_query_is_exact_lookup(self):
        planner = QueryPlanner()
        result = await planner.plan(
            query="BART by Lewis et al.",
            sources=[DataSource.SEMANTIC_SCHOLAR, DataSource.ARXIV],
        )

        assert result.intent == "exact_lookup"
        assert all("et" not in item.query.lower().split() for item in result.sub_queries)
        assert all("al" not in item.query.lower().split() for item in result.sub_queries)

    async def test_named_paper_query_is_exact_lookup(self):
        planner = QueryPlanner()
        result = await planner.plan(
            query="the Multi-news fabri2019multinews paper",
            sources=[DataSource.ARXIV, DataSource.CROSSREF],
        )

        assert result.intent == "exact_lookup"
        assert all(item.query == "Multi news" for item in result.sub_queries)

    @pytest.mark.parametrize(
        "query",
        [
            "SPIKE syntactic search paper",
            "the paper about the Objaverse dataset",
        ],
    )
    async def test_short_named_paper_forms_are_exact_lookup(self, query):
        planner = QueryPlanner()
        result = await planner.plan(
            query=query,
            sources=[DataSource.SEMANTIC_SCHOLAR, DataSource.ARXIV],
        )

        assert result.intent == "exact_lookup"


def _model_plan(source="openalex", query="federated learning privacy"):
    return json.dumps({
        "sub_queries": [{"source": source, "query": query, "rationale": "Semantic decomposition"}],
        "keywords": ["federated learning", "privacy"], "strategy": "Focused comparison",
        "intent": "methodology_survey",
    })


@pytest.mark.parametrize("query", [
    "transformer attention mechanism", "deep learning for NLP", "RAG",
    "大语言模型在食品制作方面的应用", "食品保鲜", "2020-2024 ACL Transformer",
    "the AlphaGeometry paper", "doi:10.1038/s41586-023-06747-5",
])
async def test_auto_short_topics_do_not_attempt_model(query):
    gateway = AsyncMock()
    planner = QueryPlanner(gateway)
    result = await planner.plan(query, [DataSource.OPENALEX])
    gateway.chat.assert_not_awaited()
    assert planner.planning_mode == "rules"
    assert "规则直检" in result.strategy
    assert result.sub_queries and result.original_query == query


async def test_rules_mode_keeps_constraints_without_model_even_for_complex_request():
    gateway = AsyncMock()
    planner = QueryPlanner(gateway)
    explicit = Constraint(key="min_citations", operator="gte", value=10)
    result = await planner.plan(
        "比较 2020-2024 ACL 或 EMNLP 的 Transformer 方法，并排除未开放获取的论文",
        [DataSource.OPENALEX], [explicit], planning_mode="rules",
    )
    gateway.chat.assert_not_awaited()
    assert explicit in result.constraints
    assert {(c.operator, c.value) for c in result.constraints if c.key == "year"} == {("gte", 2020), ("lte", 2024)}
    assert "不调用规划模型" in result.strategy


@pytest.mark.parametrize("query,mode", [
    ("RAG", "ai"),
    ("比较联邦学习与集中学习，排除没有隐私评估的研究", "auto"),
    ("How do graph neural networks compare with transformers for traffic prediction?", "auto"),
])
async def test_semantic_planning_is_single_budgeted_attempt(query, mode):
    gateway = AsyncMock()
    gateway.chat.return_value = _model_plan()
    planner = QueryPlanner(gateway)
    result = await planner.plan(query, [DataSource.OPENALEX], planning_mode=mode)
    gateway.chat.assert_awaited_once()
    assert gateway.chat.call_args.kwargs["timeout_seconds"] == 20.0
    assert gateway.chat.call_args.kwargs["allow_fallback"] is False
    assert planner.planning_mode == "ai" and "AI 规划完成" in result.strategy


@pytest.mark.parametrize("response", [
    "", "{}", "not json", "[]",
    _model_plan(query="   "), _model_plan(query="a" * 2001), _model_plan(source="unselected-source"),
])
async def test_invalid_ai_plan_uses_rules_without_claiming_ai_success(response):
    gateway = AsyncMock()
    gateway.chat.return_value = response
    planner = QueryPlanner(gateway)
    result = await planner.plan("federated learning", [DataSource.OPENALEX], planning_mode="ai")
    assert planner.planning_mode == "fallback"
    assert "已使用规则检索" in result.strategy and "可能产生费用" in result.strategy
    assert result.sub_queries[0].query
    gateway.chat.assert_awaited_once()


async def test_planning_timeout_cancels_attempt_and_reuses_existing_rule_plan(monkeypatch):
    cancelled = asyncio.Event()

    async def slow_model(**kwargs):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    gateway = AsyncMock()
    gateway.chat.side_effect = slow_model
    planner = QueryPlanner(gateway)
    monkeypatch.setattr(planner, "PLAN_TIMEOUT_SECONDS", 0.01)
    result = await planner.plan("federated learning", [DataSource.OPENALEX], planning_mode="ai")
    assert cancelled.is_set()
    assert planner.planning_mode == "fallback" and result.sub_queries
    gateway.chat.assert_awaited_once()


async def test_cancelling_search_does_not_start_fallback_plan():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_model(**kwargs):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    gateway = AsyncMock()
    gateway.chat.side_effect = pending_model
    planner = QueryPlanner(gateway)
    task = asyncio.create_task(planner.plan("RAG", [DataSource.OPENALEX], planning_mode="ai"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set() and planner.planning_mode != "fallback"
    gateway.chat.assert_awaited_once()


async def test_ai_source_queries_are_nonempty_selected_and_bounded_per_source():
    gateway = AsyncMock()
    gateway.chat.return_value = json.dumps({"sub_queries": [
        {"source": "openalex", "query": "privacy preserving learning"},
        {"source": "openalex", "query": "duplicate call not needed"},
        {"source": "crossref", "query": " "},
        {"source": "arxiv", "query": "not selected"},
    ]})
    explicit = Constraint(key="year", operator="gte", value=2020)
    result = await QueryPlanner(gateway).plan(
        "federated learning", [DataSource.OPENALEX, DataSource.CROSSREF], [explicit], planning_mode="ai",
    )
    assert len(result.sub_queries) == 2
    assert {item.source for item in result.sub_queries} == {DataSource.OPENALEX, DataSource.CROSSREF}
    assert all(item.query.strip() for item in result.sub_queries)
    assert explicit in result.constraints
