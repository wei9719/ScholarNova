"""Deterministic tests for FTI-3A answer verification."""

import pytest

from app.services.inference import build_retrieval_fallback, verify_answer_citations


@pytest.mark.parametrize("heading", [
    "原文信息（严格依据材料）", "教学解释（非论文原文）",
    "### 原文信息（严格依据材料）", "**教学解释（非论文事实）**",
    "一、原文信息（严格依据材料）", "### 教学解释（非论文原文） ###",
    "Source information (from supplied material)", "Teaching explanation (not paper findings)",
])
def test_pure_source_or_teaching_headings_do_not_create_uncited_claims(heading):
    answer = f"{heading}\n论文中的实验保留独立的评价范围。[S1]\n当前材料需要按来源编号核对事实。[S1]"
    result = verify_answer_citations(answer, ["S1"])
    assert result.status == "verified"
    assert result.claim_count == result.cited_claim_count == 2


@pytest.mark.parametrize("uncited", [
    "教学解释：该论文的MAE降低到14.36。",
    "### 教学解释（MAE为14.36）",
    "原文信息（已核对第8页）",
    "教学解释（作者明确证明模型最优）",
    "说明：教学解释证明该模型的MAE为14.36。",
    "Note: teaching explanation confirms an error of 14.36.",
    "Teaching explanation: the authors verified these results.",
])
def test_teaching_facts_numbers_and_source_claims_still_require_citations(uncited):
    result = verify_answer_citations("论文描述了可核对的评价范围。[S1]\n" + uncited, ["S1"])
    assert result.status != "verified"
    assert result.claim_count == 2 and result.uncited_claim_count == 1

@pytest.mark.parametrize("question", ["总结研究空白", "Summarize the evidence"])
def test_multisentence_fallback_cites_each_segment(question):
    evidence = [
        ("S1", "An overview. Further studies!", "第一项实验测试检索效果。第二项实验仍需跨领域验证！\n第三项实验有数据局限。[S99]"),
        ("S2", "Dr. Lee's study", "This method uses retrieval. It preserves page numbers. More data is required."),
    ]
    answer = build_retrieval_fallback(question, evidence, reason="citation_verification")
    verification = verify_answer_citations(answer, ["S1", "S2"])
    assert verification.status == "verified"
    assert verification.coverage == 1
    assert verification.uncited_claim_count == 0
    assert "[S99]" not in answer
    assert "模型暂时不可用" not in answer
    assert "model is temporarily unavailable" not in answer


def test_fallback_preserves_decimal_scores_versions_dois_and_urls():
    title = "Evaluation v2.5"
    content = (
        "The F1 score was 0.38. Precision was 81.5%. "
        "See DOI 10.1000/article.v2.5 and https://doi.org/10.1000/article.v2.5. "
        "More results: https://example.org/evaluate?version=2.5&f1=0.38."
    )
    answer = build_retrieval_fallback("Summarize evidence", [("S1", title, content)])

    assert title in answer
    for literal in (
        "0.38", "81.5%", "10.1000/article.v2.5",
        "https://doi.org/10.1000/article.v2.5",
        "https://example.org/evaluate?version=2.5&f1=0.38",
    ):
        assert literal in answer
    verification = verify_answer_citations(answer, ["S1"])
    assert verification.status == "verified"
    assert verification.claim_count == 4
    assert verification.coverage == 1.0


def test_verifier_counts_decimal_sentences_without_hiding_uncited_claims():
    result = verify_answer_citations(
        "The F1 score was 0.38. [S1] Precision was 81.5%.", ["S1"],
    )
    assert result.status == "partial"
    assert result.claim_count == 2
    assert result.uncited_claim_count == 1


def test_verifier_accepts_complete_valid_citations() -> None:
    result = verify_answer_citations(
        "方法采用混合检索。[S1]\n实验结果显示召回率提升。[S2]",
        ["S1", "S2"],
    )

    assert result.status == "verified"
    assert result.coverage == 1.0
    assert result.uncited_claim_count == 0
    assert result.used_citation_ids == ("S1", "S2")


def test_verifier_marks_uncited_claims_as_partial() -> None:
    result = verify_answer_citations(
        "方法采用混合检索。[S1]\n实验仍需要跨领域验证。",
        ["S1"],
    )

    assert result.status == "partial"
    assert result.coverage == 0.5
    assert result.uncited_claim_count == 1


def test_verifier_rejects_unknown_source_ids() -> None:
    result = verify_answer_citations(
        "The method improves traceability. [S9]",
        ["S1"],
    )

    assert result.status == "failed"
    assert result.invalid_citation_ids == ("S9",)


def test_retrieval_fallback_remains_fully_traceable() -> None:
    answer = build_retrieval_fallback(
        "请总结证据",
        [
            ("S1", "第一篇论文", "研究采用可追溯证据片段。"),
            ("S2", "第二篇论文", "研究保留页码和章节定位。"),
        ],
    )
    result = verify_answer_citations(answer, ["S1", "S2"])

    assert result.status == "verified"
    assert result.coverage == 1.0
    assert "[S1]" in answer
    assert "[S2]" in answer
