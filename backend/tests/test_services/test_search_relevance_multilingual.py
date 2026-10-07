"""Offline relevance regressions: topic evidence is not publication quality."""

import uuid

import pytest

from app.schemas.paper import Paper
from app.schemas.query import DataSource
from app.services.search.query_planner import QueryPlanner
from app.services.search.ranker import Ranker


def paper(title, abstract="", **kwargs):
    return Paper(id=str(uuid.uuid4()), title=title, abstract=abstract,
                 authors=[], source="test", **kwargs)


@pytest.mark.parametrize("title", [
    "交通流数据修复", "Traffic flow data imputation",
    "Missing traffic flow data imputation with deep learning",
])
async def test_explicit_topic_and_task_in_title_need_no_abstract(title):
    assert await Ranker()._calculate_relevance(paper(title), "交通流数据修复") >= 0.9


@pytest.mark.parametrize("title", [
    "基于 DeepSeek 的交通流预测", "DeepSeek for traffic flow forecasting",
])
async def test_same_topic_different_task_is_neighbor_not_exact(title):
    ranker = Ranker()
    direct = await ranker._calculate_relevance(paper("Traffic flow imputation"), "交通流数据修复")
    neighbor = await ranker._calculate_relevance(paper(title), "交通流数据修复")
    assert 0.45 <= neighbor < 0.7
    assert direct > neighbor


@pytest.mark.parametrize("title,abstract", [
    ("装配式H型钢组合梁抗弯性能", "试验数据验证了计算模型与修复方法。"),
    ("Data-driven prefabricated H-shaped steel beam repair", "data model methods"),
    ("Quantum chemistry", "data repair models"),
])
async def test_generic_data_and_shared_task_cannot_replace_topic(title, abstract):
    assert await Ranker()._calculate_relevance(paper(title, abstract), "交通流数据修复") < 0.25


@pytest.mark.parametrize("query,title", [
    ("医疗数据修复", "Medical data imputation"),
    ("图像修复", "Image restoration"),
    ("Traffic flow forecasting", "交通流预测"),
    ("DeepSeek 与 Qwen 交通流数据修复", "DeepSeek and Qwen for traffic flow imputation"),
])
async def test_cross_language_matching_is_symmetric_and_compositional(query, title):
    assert await Ranker()._calculate_relevance(paper(title), query) >= 0.9


async def test_mixed_model_names_do_not_mask_different_topic_or_task():
    ranker = Ranker()
    query = "DeepSeek 与 Qwen 交通流数据修复"
    direct = paper("DeepSeek and Qwen for traffic flow imputation")
    neighbor = paper("DeepSeek and Qwen for traffic flow prediction")
    wrong = paper("DeepSeek and Qwen for steel beam repair")
    scores = [await ranker._calculate_relevance(item, query) for item in (direct, neighbor, wrong)]
    assert scores[0] >= 0.9
    assert 0.45 <= scores[1] < 0.7
    assert scores[2] < scores[1]


async def test_unknown_chinese_subject_is_not_discarded_for_data_model():
    query = "寒旱生态遥感数据修复"
    translated = QueryPlanner._translate_to_english(query)
    assert "寒旱生态遥感" in translated
    plan = await QueryPlanner().plan(query, [DataSource.SEMANTIC_SCHOLAR,
        DataSource.OPENALEX, DataSource.CROSSREF, DataSource.ARXIV], planning_mode="rules")
    assert all("寒旱生态遥感" in item.query for item in plan.sub_queries)
    assert "未翻译" in plan.strategy
    direct = await Ranker()._calculate_relevance(paper(query), query)
    generic = await Ranker()._calculate_relevance(paper("Data repair models"), query)
    assert direct >= 0.9 and generic < 0.25


@pytest.mark.parametrize("query,expected", [
    ("图神经网络", "graph neural network"),
    ("引文网络", "citation network"),
    ("交通流数据修复", "traffic flow data imputation"),
])
def test_translation_uses_longest_terms_without_merging_words(query, expected):
    assert QueryPlanner._translate_to_english(query) == expected


@pytest.mark.parametrize("query,title", [("graph", "Paragraph analysis"), ("data", "Database systems")])
async def test_latin_substrings_are_not_word_matches(query, title):
    assert await Ranker()._calculate_relevance(paper(title), query) == 0


async def test_popularity_and_mmr_cannot_promote_off_topic_candidate():
    ranker = Ranker()
    direct = [paper("Traffic flow imputation", year=2018) for _ in range(3)]
    neighbor = paper("DeepSeek traffic flow prediction", year=2018)
    unrelated = paper("Data-driven steel beam repair", citation_count=999999,
                      year=2026, is_open_access=True)
    ranked = await ranker.rank([unrelated, neighbor, *direct], "交通流数据修复",
        source_ranks={"test": {str(unrelated.id): 1}}, limit=5)
    assert {item.id for item in ranked[:3]} == {item.id for item in direct}
    assert ranked[3].id == neighbor.id and ranked[4].id == unrelated.id
    assert unrelated.relevance_score < 0.25
