"""Paper locators are deterministic, bounded and unrelated to any fixed answer."""

import pytest

from app.services.retrieval.paper_locator import (
    expand_research_query,
    match_named_papers,
    normalize_query,
    parse_locator,
    table_matches,
)


@pytest.mark.parametrize("question,page,table", [
    ("STD-PLM 第8页Table2 RM70 CM70", 8, 2),
    ("Please read GATGPT page 8 Table 2", 8, 2),
    ("GATGPT p.8 tab. II", 8, 2),
    ("第八页的表二", 8, 2),
    ("第十二页表十", 12, 10),
    ("Page8 Table20", 8, 20),
    ("第0页Table0", 0, 0),
    ("比较总体方法", None, None),
])
def test_page_table_forms(question, page, table):
    locator = parse_locator(question)
    assert (locator.page, locator.table) == (page, table)


def test_exact_table_number_accepts_whitespace_but_not_a_longer_number():
    assert table_matches("Table\n2: Imputation results", 2)
    assert table_matches("TABLE2: Results", 2)
    assert table_matches("表二 结果", 2)
    assert table_matches("Table II Results", 2)
    assert not table_matches("Table20: Results", 2)
    assert not table_matches("stable2 model", 2)


def test_compact_labels_normalize_without_rewriting_chinese_punctuation():
    assert normalize_query("STD−PLM Table2 RM70、CM 70%，说明限制。") == "STD-PLM Table 2 RM 70%、CM 70%，说明限制。"
    question = "把刚才的限制变成两项任务，不要重述整段回答。"
    assert normalize_query(question) == question


@pytest.mark.parametrize("question,expected", [
    ("STD-PLM 第8页", ["std"]),
    ("STDPLM 第8页", ["std"]),
    ("STD PLM 第8页", ["std"]),
    ("GATGPT的图有什么作用？", ["gat"]),
    ("Explain Attention Is All You Need", ["attention"]),
    ("How does Deep Learning for Multivariate Time Series work?", ["survey"]),
    ("XGATGPT is a different identifier", []),
    ("This is about spatial temporal forecasting", []),
])
def test_distinctive_title_prefixes_and_whole_titles(question, expected):
    titles = [
        ("std", "STD-PLM: Understanding Both Spatial and Temporal Properties"),
        ("gat", "GATGPT: A Pretrained Language Model for Traffic"),
        ("attention", "Attention Is All You Need"),
        ("survey", "Deep Learning for Multivariate Time Series Imputation: A Survey"),
    ]
    assert match_named_papers(question, titles) == expected


def test_explicit_comparison_keeps_both_named_papers():
    assert set(match_named_papers("比较GATGPT与STD-PLM", [
        ("std", "STD-PLM: Spatial Temporal Model"), ("gat", "GATGPT: Traffic Model"),
    ])) == {"std", "gat"}


def test_model_name_prefix_can_use_hyphen_or_joined_spelling():
    assert match_named_papers("Time-LLM的原文", [("time", "TimeLLM: Time Series Forecasting")]) == ["time"]
    assert match_named_papers("TimeLLM的原文", [("time", "Time-LLM: Time Series Forecasting")]) == ["time"]


def test_complete_title_disambiguates_shared_prefix_but_explicit_comparison_keeps_both():
    original = "MOMENT: A Family of Open Time-series Foundation Models"
    other = "MOMENT: A Different Method for Another Task"
    titles = [("original", original), ("other", other)]
    assert match_named_papers(f"{original} page 2", titles) == ["original"]
    assert set(match_named_papers(f"Compare {original} with {other}", titles)) == {"original", "other"}
    assert set(match_named_papers("MOMENT page 2", titles)) == {"original", "other"}


def test_bilingual_expansion_is_bounded_literal_and_does_not_invent_paper_names():
    question = "对比AlphaModel和BetaModel的数据集、缺失设置、评价指标与实验协议，分开预测与插补。"
    expanded = expand_research_query(question)
    assert expanded.startswith(question + "\n")
    assert all(term in expanded for term in ("dataset", "missing", "metrics", "settings", "forecasting", "imputation"))
    assert len(expanded) - len(question) < 250
    assert expanded.count("AlphaModel") == expanded.count("BetaModel") == 1
    assert expand_research_query("讨论伦理问题") == "讨论伦理问题"
