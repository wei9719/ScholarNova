"""Search precision regressions: offline metadata and mocked real request paths."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import search
from app.models.search_run import SearchRun
from app.schemas.paper import Paper
from app.schemas.query import Constraint, DataSource
from app.schemas.search import SearchRequest
from app.services.search.query_planner import QueryPlanner
from app.services.search.ranker import Ranker
from app.services.search.retriever import Retriever, RetrieveResult, SourceStatus
from app.services.sources.openalex import OpenAlexSource

QUESTION = "交通流数据在大语言模型下的缺失修复"
LONG_QUESTION = "2023年至今使用预训练大语言模型修复道路交通传感器缺失数据，而不是只做预测的研究"


def paper(identifier, title, abstract=None, **kwargs):
    return Paper(id=identifier, title=title, abstract=abstract, source="openalex", **kwargs)


@pytest.mark.parametrize(
    "name,minimum,maximum",
    [("GSF-LLM", 0.0, 0.35), ("GATGPT", 0.7, 1.0), ("STD-PLM", 0.45, 0.69),
     ("NuwaTS", 0.45, 0.69)],
)
async def test_v4_live_metadata_distinguishes_current_imputation_from_future_extension(
    name, minimum, maximum,
):
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "search_task_claims_v4.json").read_text(
            encoding="utf-8"
        )
    )
    record = next(item for item in fixture["papers"] if item["title"].startswith(name))
    observed = Paper.model_validate(record)
    ranker = Ranker()
    score = await ranker._calculate_relevance(observed, fixture["query"])
    assert minimum <= score <= maximum
    preference = Constraint(key="task_type", operator="eq", value="imputation")
    assert ranker._calculate_constraint_score(observed, [preference]) == (
        0.0 if name == "GSF-LLM" else 1.0
    )
    ranked = await ranker.rank([observed], fixture["query"], minimum_relevance=0.45)
    assert bool(ranked) is (name != "GSF-LLM")


@pytest.mark.parametrize(
    "abstract,expected",
    [
        ("Experiments show improved traffic prediction, showing strong potential for extension "
         "to data imputation, trajectory generation, and anomaly detection.", "prospective_only"),
        ("Future work will explore data imputation.", "prospective_only"),
        ("Our model can be extended to imputation.", "prospective_only"),
        ("Our model could impute missing traffic data.", "prospective_only"),
        ("In future work, our model may support imputation.", "prospective_only"),
        ("Future work makes our model capable of implementing imputation.", "prospective_only"),
        ("Data imputation remains future work.", "prospective_only"),
        ("Our model performs forecasting, and imputation is left for future work.",
         "prospective_only"),
        ("Our model performs forecasting, with potential applications in data imputation.",
         "prospective_only"),
        ("未来工作将探索交通流数据插补。", "prospective_only"),
        ("本文实现交通流预测，未来可扩展到缺失值插补。", "prospective_only"),
        ("We propose a model for traffic imputation. Future work will expand forecasting.",
         "current"),
        ("We propose traffic imputation, while future work extends imputation to other domains.",
         "current"),
        ("Future work explores forecasting, whereas we demonstrate traffic imputation now.",
         "current"),
        ("Our model is capable of implementing both forecasting and imputation tasks.", "current"),
        ("Once trained, our model can be applied to impute missing data across domains.", "current"),
        ("We train language models to recover missing traffic values.", "current"),
        ("We evaluate not only imputation but also forecasting.", "current"),
        ("本文提出交通流缺失值插补方法，实验结果表明方法有效。", "current"),
        ("Our model does not support imputation.", "unknown"),
        ("Our model is not evaluated on imputation.", "unknown"),
        ("Imputation is an important problem in transportation.", "unknown"),
        ("We propose graph repair for traffic forecasting, showing potential for extension "
         "to data imputation.", "prospective_only"),
        ("", "unknown"),
    ],
)
def test_paper_task_claim_scope_preserves_present_dual_tasks_and_unknown(abstract, expected):
    ranker = Ranker()
    observed = paper("scope", "A traffic language model", abstract)
    assert ranker._paper_task_claim(observed.title, abstract, "imputation") == expected
    preference = Constraint(key="task_type", operator="eq", value="imputation")
    assert ranker._calculate_constraint_score(observed, [preference]) == (
        1.0 if expected == "current" else 0.0
    )
    assert ranker.filter_hard_constraints([observed], [preference]) == [observed]


def test_paper_task_claim_does_not_modify_future_research_query_intent():
    assert QueryPlanner.search_facets("未来我想研究交通流大语言模型插补")["task"] == "imputation"
    assert Ranker._paper_task_claim("Future work on LLM traffic imputation", "", "imputation") == (
        "prospective_only"
    )


@pytest.mark.parametrize("query", [QUESTION, LONG_QUESTION])
async def test_rules_keep_domain_task_method_without_scaffolding(query):
    plan = await QueryPlanner().plan(
        query, [DataSource.OPENALEX, DataSource.CROSSREF], planning_mode="rules"
    )
    oa = next(sq.query for sq in plan.sub_queries if sq.source == DataSource.OPENALEX)
    assert "下的" not in " ".join(sq.query for sq in plan.sub_queries)
    assert "2023" not in oa and "预测" not in oa and "prediction" not in oa
    assert '"imputation" OR' in oa and 'OR "data recovery"' in oa
    assert '"large language model" OR' in oa and 'OR "PLM"' in oa
    assert "traffic" in oa and " AND " in oa
    normalized = QueryPlanner.ranking_query(plan)
    assert "2023" not in normalized and "下的" not in normalized
    assert "prediction" not in normalized and "而不" not in normalized


async def test_ai_synonyms_are_not_required_together_and_prediction_only_is_not_a_blanket_not():
    gateway = AsyncMock()
    gateway.chat.return_value = json.dumps(
        {
            "sub_queries": [
                {
                    "source": "openalex",
                    "query": "pre-trained large language model traffic sensor data imputation recovery NOT prediction",
                }
            ],
            "keywords": ["large language model", "traffic sensor", "imputation"],
            "constraints": [{"key": "task_type", "operator": "eq", "value": "imputation"}],
            "intent": "literature_review",
        }
    )
    planner = QueryPlanner(gateway)
    plan = await planner.plan(LONG_QUESTION, [DataSource.OPENALEX], planning_mode="ai")
    compiled = plan.sub_queries[0].query
    assert "NOT" not in compiled and "prediction" not in compiled
    assert "sensor AND recovery" not in compiled
    assert 'OR "data recovery"' in compiled and 'OR "PLM"' in compiled
    refinements = planner.build_refinement_subqueries(plan, [DataSource.OPENALEX])
    assert len(refinements) == 1
    assert "traffic" in refinements[0].query and "imputation" in refinements[0].query
    assert '"large language model"' not in refinements[0].query
    gateway.chat.assert_awaited_once()


def test_ai_boolean_synonym_groups_are_not_flattened_into_required_domain_words():
    compiled = QueryPlanner._openalex_query(
        "(traffic OR transportation) AND (imputation OR recovery) AND (LLM OR PLM)",
        original=QUESTION,
    )
    assert "traffic AND transportation" not in compiled
    assert "imputation AND recovery" not in compiled
    assert "(traffic OR transportation OR road)" in compiled
    assert '"imputation" OR' in compiled and 'OR "PLM"' in compiled


async def test_explicit_user_boolean_not_is_preserved_and_not_scored_as_positive():
    query = "asthma NOT mouse"
    plan = await QueryPlanner().plan(query, [DataSource.OPENALEX], planning_mode="rules")
    assert plan.sub_queries[0].query == query
    ranked = await Ranker().rank(
        [
            paper("human", "Asthma treatment in adults"),
            paper("excluded", "Asthma treatment in mouse models", citation_count=999999),
        ],
        QueryPlanner.ranking_query(plan),
        minimum_relevance=0.45,
    )
    assert [p.id for p in ranked] == ["human"]


async def test_actual_model_task_domain_evidence_outranks_popularity_and_neighbours():
    direct = paper("direct", "Large language models for traffic speed imputation")
    baseline = paper("baseline", "Missing traffic flow imputation with LSTM")
    transfer = paper("transfer", "Pretrained language model for spatiotemporal imputation")
    forecast = paper(
        "forecast", "Large language models for traffic prediction", citation_count=999999
    )
    unrelated = [
        paper(
            "health",
            "Cognitive functional therapy with movement sensor biofeedback for low back pain",
            citation_count=999999,
            year=2026,
        ),
        paper(
            "steel", "Large language model for steel beam repair", citation_count=999999, year=2026
        ),
        paper("zero", "A study of digital forensics", citation_count=999999, year=2026),
    ]
    ranked = await Ranker().rank(
        [*unrelated, forecast, baseline, transfer, direct], QUESTION, minimum_relevance=0.45
    )
    assert ranked[0].id == "direct" and ranked[0].relevance_score >= 0.7
    assert {p.id for p in ranked} == {"direct", "baseline", "transfer"}
    assert 0.45 <= transfer.relevance_score < 0.7  # no traffic claim from an absent domain
    assert direct.abstract is None  # title evidence does not create an abstract


async def test_task_type_is_evidence_preference_not_a_hard_failure_for_missing_abstract():
    ranker = Ranker()
    both = paper("both", "A unified language model for forecasting and imputation")
    only_prediction = paper("prediction", "Traffic speed prediction with language models")
    unknown = paper("unknown", "A unified spatiotemporal model")
    constraint = Constraint(key="task_type", operator="eq", value="imputation")
    assert ranker.filter_hard_constraints([both, only_prediction, unknown], [constraint]) == [
        both,
        only_prediction,
        unknown,
    ]
    assert ranker._calculate_constraint_score(both, [constraint]) == 1.0
    assert ranker._calculate_constraint_score(unknown, [constraint]) == 0.0
    assert unknown.abstract is None


async def test_model_domain_with_unknown_task_is_only_a_partial_candidate():
    unknown = paper("unknown", "Large language models for road traffic applications")
    ranker = Ranker()
    score = await ranker._calculate_relevance(unknown, QUESTION)
    assert 0.45 <= score < 0.7
    assert unknown.abstract is None


@pytest.mark.parametrize(
    "query,title",
    [
        ("transformer attention mechanism", "Transformer attention mechanism"),
        ("医疗数据修复", "Medical data imputation"),
        ("寒旱生态遥感数据修复", "寒旱生态遥感数据修复"),
        ("地下水数据修复", "地下水数据修复"),
    ],
)
async def test_ordinary_topics_and_unknown_chinese_subjects_stay_usable(query, title):
    ranked = await Ranker().rank(
        [paper("match", title), paper("wrong", "Quantum chemistry")], query, minimum_relevance=0.45
    )
    assert [p.id for p in ranked] == ["match"]
    assert ranked[0].relevance_score >= 0.7


async def test_openalex_mock_request_retains_fulltext_scope_and_structured_date_filter():
    source = OpenAlexSource()
    response = MagicMock()
    response.json.return_value = {"results": []}
    source._request_with_retry = AsyncMock(return_value=response)
    query = QueryPlanner._openalex_query(QUESTION)
    await source.search(query, 15, date_from="2023-01-01", date_to="2026-10-17")
    params = source._request_with_retry.await_args.kwargs["params"]
    assert params["search"] == query and "search.title_and_abstract" not in params
    assert params["filter"] == "from_publication_date:2023-01-01,to_publication_date:2026-10-17"


@pytest.mark.parametrize("recover", [True, False])
async def test_many_off_topic_api_rows_trigger_only_existing_second_round_and_never_fill_results(
    test_engine,
    monkeypatch,
    recover,
):
    from app import database
    from app.core import cache
    from app.services import inference

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as db:
        db.add(SearchRun(id="precision", raw_query=QUESTION, status="pending"))
        await db.commit()
    unrelated = [
        paper(f"wrong-{i}", f"Low back pain clinical trial {i}", year=2026, citation_count=999999)
        for i in range(30)
    ]
    target = paper("target", "Large language models for traffic flow imputation", year=2025)
    source_status = [SourceStatus(source="openalex", success=True, paper_count=30)]
    retrieve = AsyncMock(
        side_effect=[
            RetrieveResult(papers=unrelated, source_statuses=source_status),
            RetrieveResult(
                papers=[target] if recover else unrelated, source_statuses=source_status
            ),
        ]
    )
    monkeypatch.setattr(database, "async_session_factory", factory)
    gateway = SimpleNamespace(usage={"total_tokens": 0}, chat=AsyncMock())
    monkeypatch.setattr(inference, "RoutedLLMGateway", lambda **_: gateway)
    monkeypatch.setattr(Retriever, "retrieve", retrieve)
    saved = {}

    async def cache_set(key, value, ttl):
        assert ttl == 3600
        saved[key] = value

    monkeypatch.setattr(
        cache,
        "CacheManager",
        lambda: SimpleNamespace(
            set=cache_set, get=AsyncMock(side_effect=lambda key: saved.get(key))
        ),
    )
    await search._execute_search_task(
        "precision",
        SearchRequest(
            query=QUESTION,
            sources=[DataSource.OPENALEX],
            planning_mode="rules",
            max_results=15,
        ),
    )
    assert retrieve.await_count == 2
    assert "imputation" in retrieve.await_args.kwargs["sub_queries"][0].query
    gateway.chat.assert_not_awaited()
    async with factory() as db:
        detail = await search.get_search_run("precision", db)
        assert detail.status == "completed"
        assert [p.id for p in detail.results] == (["target"] if recover else [])
        assert detail.runtime_metrics["search_rounds"] == 2
        assert "可以少于" in detail.result_summary["relevance_note"]
        assert "跨域方法" in detail.result_summary["relevance_note"]
