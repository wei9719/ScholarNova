"""Old PDFs and explicit page/table evidence must survive larger local libraries."""

from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import agent
from app.models.knowledge import KnowledgeBase
from app.models.paper import PaperChunk, PaperEntity
from app.services.inference.model_router import RoutedChatResult

SCOPE = "本次研究"
STD_TITLE = "STD-PLM: Understanding Both Spatial and Temporal Properties"


def chunk(pid, position, content, *, page=1, year=2020):
    return PaperChunk(
        id=f"{pid}-{position}", paper_id=pid, position=position, page=page,
        kind="fulltext", heading="Full text", content=content,
        content_hash=f"hash-{pid}-{position}", feature_version="pdf-parser-chunker-v3",
        char_count=len(content), created_at=datetime(year, 1, 1),
    )


@pytest.fixture
def offline_agent(monkeypatch, db_session):
    captured = []

    async def answer(**kwargs):
        assert not db_session.in_transaction(), "Release the preparation transaction before model waiting"
        captured.append(kwargs["messages"])
        return RoutedChatResult(
            content="当前回答仅依据提供的材料。[S1]",
            profile={"provider": "custom", "model": "offline"},
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20, "requests": 1},
            attempts=(), fallback_used=False,
        )

    model = AsyncMock(side_effect=answer)
    zotero = AsyncMock(side_effect=AssertionError("A located-paper request must not expand to Zotero"))
    monkeypatch.setattr(agent, "chat_with_fallback", model)
    monkeypatch.setattr(agent.ZoteroLocalClient, "search_items", zotero)
    monkeypatch.setattr(agent, "get_model_for_task", lambda _: {"provider": "custom", "model": "offline"})
    monkeypatch.setattr(agent, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.retrieval.hybrid.get_embedding_config", lambda: {"enabled": False})
    return model, zotero, captured


async def seed(db):
    db.add_all([
        PaperEntity(id="std", title=STD_TITLE, source="test"),
        PaperEntity(id="gat", title="GATGPT: A Pretrained Model for Traffic", source="test"),
        PaperEntity(id="private", title="PrivateGPT: Hidden source", source="test"),
        PaperEntity(id="notes-only", title="NoteGPT: A Paper Without Imported PDF", source="test"),
    ])
    await db.flush()
    db.add_all([
        chunk("std", 33, "RM (random missing) and CM (continuity missing) are different. PAGE8_CONTEXT", page=8),
        chunk("std", 34, "Table 1: Forecasting. PAGE8_FORECASTING", page=8),
        chunk("std", 35, "Table 2: Imputation performance. RM 70% CM 70% MAE RMSE. STD-PLM 14.36 23.20 22.69 39.66. TABLE2_RAW", page=8),
        chunk("std", 36, "Table20: Not the requested table. OTHER_PAGE", page=9),
        chunk("gat", 0, "Table II: GATGPT results. GAT_PAGE2", page=2),
        chunk("private", 0, "Table 2: PRIVATE_ONLY", page=8),
    ])
    for pid, category in [("std", SCOPE), ("gat", SCOPE), ("private", "另一个分类"), ("notes-only", SCOPE)]:
        db.add(KnowledgeBase(
            id=f"note-{pid}", title=f"Note for {pid}", category=category,
            source_paper_title=STD_TITLE if pid == "std" else f"{'NoteGPT' if pid == 'notes-only' else 'GATGPT'}: Source paper",
            source_paper_id=pid, content=f"整理笔记 {pid}。NOT_PDF_{pid}",
            updated_at=datetime(2020, 1, 1),
        ))
    db.add(KnowledgeBase(
        id="unlinked", title="STD-PLM Table2 RM70 CM70", category=SCOPE,
        content="UNLINKED_FAKE_VALUES 9999", updated_at=datetime(2026, 1, 1),
    ))
    db.add(PaperEntity(id="new-long", title="New unrelated study", source="test"))
    await db.flush()
    db.add(KnowledgeBase(id="new-note", title="New note", category=SCOPE, source_paper_id="new-long", content="NEW_ONLY"))
    db.add_all(chunk("new-long", i, f"Unrelated new material {i} NEW_ONLY", year=2026) for i in range(700))
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "STD-PLM原文第8页Table2的RM70和CM70结果是什么？",
    "STDPLM page8 Table 2 RM 70% CM 70%",
    "STD PLM第八页表二，请核对数值。",
])
async def test_old_named_pdf_table_contents_override_recent_cutoff_and_notes(client, db_session, offline_agent, question):
    model, zotero, captured = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={"question": question, "knowledge_category": SCOPE})
    assert response.status_code == 200
    body = response.json()
    assert body["citations"][0]["source"] == "paper"
    assert body["citations"][0]["item_id"] == "std"
    assert body["citations"][0]["page"] == 8
    prompt = captured[0][-1]["content"]
    assert "TABLE2_RAW" in prompt and "14.36 23.20 22.69 39.66" in prompt
    assert "NEW_ONLY" not in prompt and "UNLINKED_FAKE_VALUES" not in prompt
    assert "GAT_PAGE2" not in prompt and "PRIVATE_ONLY" not in prompt and "OTHER_PAGE" not in prompt
    step = next(step for step in body["tool_steps"] if step["tool"] == "paper_fulltext_search")
    assert step["count"] == 3 and "指定论文页/表定向" in step["detail"]
    assert "705" in step["detail"]  # 700 recent + 4 STD + 1 GAT; private excluded
    model.assert_awaited_once()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
async def test_page_only_includes_late_table_text_not_just_first_two_chunks(client, db_session, offline_agent):
    _, _, captured = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={"question": "STD-PLM第8页说了什么？", "knowledge_category": SCOPE})
    assert response.status_code == 200
    assert "TABLE2_RAW" in captured[0][-1]["content"]
    assert all(c["page"] == 8 for c in response.json()["citations"] if c["source"] == "paper")


@pytest.mark.asyncio
async def test_other_paper_and_roman_table_locator(client, db_session, offline_agent):
    _, _, captured = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={"question": "GATGPT page2 Table2", "knowledge_category": SCOPE})
    assert response.status_code == 200
    assert "GAT_PAGE2" in captured[0][-1]["content"]
    assert "TABLE2_RAW" not in captured[0][-1]["content"]
    assert response.json()["citations"][0]["item_id"] == "gat"


@pytest.mark.asyncio
async def test_table_without_page_prioritizes_caption_and_not_a_different_table(client, db_session, offline_agent):
    _, _, captured = offline_agent
    await seed(db_session)
    db_session.add(chunk("std", 0, "The discussion mentions Table 2 but has no results.", page=1))
    await db_session.commit()
    response = await client.post("/api/v1/agent/chat", json={"question": "STD-PLM Table2的结果？", "knowledge_category": SCOPE})
    assert response.status_code == 200
    assert response.json()["citations"][0]["page"] == 8
    assert "TABLE2_RAW" in captured[0][-1]["content"]
    assert "OTHER_PAGE" not in captured[0][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "STD-PLM第8页Table20的数值？", "STD-PLM第88页说了什么？",
    "PrivateGPT第8页Table2", "第8页Table2说了什么？", "NoteGPT第8页Table2",
    "PrivateGPT原文的方法是什么？", "《PrivateGPT: Hidden source》的方法是什么？",
])
async def test_missing_or_out_of_scope_locator_never_fills_with_other_material(client, db_session, offline_agent, question):
    model, zotero, _ = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={"question": question, "knowledge_category": SCOPE})
    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == [] and body["inference_mode"] == "none"
    assert body["grounded"] is False and "不会" in body["answer"]
    step = next(step for step in body["tool_steps"] if step["tool"] == "paper_fulltext_search")
    assert step["status"] == "unavailable" and step["count"] == 0
    model.assert_not_awaited()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
async def test_named_notes_without_pdf_remain_labelled_notes(client, db_session, offline_agent):
    _, zotero, captured = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={"question": "NoteGPT的研究是什么？", "knowledge_category": SCOPE})
    assert response.status_code == 200
    assert {c["source"] for c in response.json()["citations"]} == {"knowledge"}
    assert "知识库笔记不是PDF原文" in captured[0][-1]["content"]
    assert "NOT_PDF_notes-only" in captured[0][-1]["content"]
    assert "NEW_ONLY" not in captured[0][-1]["content"]
    zotero.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmatched_explicit_title_without_category_does_not_expand_to_zotero(client, db_session, offline_agent):
    model, zotero, _ = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={"question": "《AbsentGPT: No available source》的方法是什么？"})
    assert response.status_code == 200
    assert response.json()["citations"] == []
    model.assert_not_awaited()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_named_paper_overrides_followup_history_topic(client, db_session, offline_agent):
    _, _, captured = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={
        "question": "这篇论文的STD-PLM第8页Table2说了什么？", "knowledge_category": SCOPE,
        "history": [{"role": "user", "content": "GATGPT的方法是什么？"}],
    })
    assert response.status_code == 200
    assert "TABLE2_RAW" in captured[0][-1]["content"]
    assert "GAT_PAGE2" not in captured[0][-1]["content"]
    assert not any(step["tool"] == "contextual_query" for step in response.json()["tool_steps"])


@pytest.mark.asyncio
async def test_named_comparison_keeps_both_papers_and_document_diversity(client, db_session, offline_agent):
    _, _, _ = offline_agent
    await seed(db_session)
    db_session.add_all(chunk("gat", i, "GATGPT methods are described here.", page=2) for i in range(1, 12))
    await db_session.commit()
    response = await client.post("/api/v1/agent/chat", json={
        "question": "Compare STD-PLM and GATGPT methods", "knowledge_category": SCOPE,
    })
    assert response.status_code == 200
    paper_citations = [c for c in response.json()["citations"] if c["source"] == "paper"]
    assert {c["item_id"] for c in paper_citations} == {"std", "gat"}
    assert all(sum(c["item_id"] == pid for c in paper_citations) <= 3 for pid in ("std", "gat"))


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["那是速度还是流量数据？", "你刚才说的数据类型区别，为什么会影响评价指标？"])
async def test_pronominal_followup_keeps_named_pdf_scope(client, db_session, offline_agent, question):
    _, _, captured = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={
        "question": question, "knowledge_category": SCOPE,
        "history": [{"role": "user", "content": "GATGPT 的交通实验数据是什么？"},
                    {"role": "assistant", "content": "UNSUPPORTED_PAST_FACT 99%"}],
    })
    assert response.status_code == 200
    assert {c["item_id"] for c in response.json()["citations"] if c["source"] == "paper"} == {"gat"}
    prompt = captured[0][-1]["content"]
    assert "GAT_PAGE2" in prompt and "TABLE2_RAW" not in prompt
    assert "UNSUPPORTED_PAST_FACT" not in prompt.split("可引用材料：", 1)[1]
    assert any(step["tool"] == "contextual_query" for step in response.json()["tool_steps"])


STD_DATASET_QUESTION = "STD-PLM 的插补实验用了哪个数据集？只回答一句，标注 PDF 页码来源，不要补充教学。"
STD_TABLE_FOLLOWUP = "那 Table 2 中 RM70% 和 CM70% 分别表示什么？只回答两句，区分原文与教学，不要重复上一问的数据集。"
STD_DATASET_HISTORY = [
    {"role": "user", "content": STD_DATASET_QUESTION},
    {"role": "assistant", "content": "STD-PLM 的插补实验使用了 PEMS08 数据集 [S2]。"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_after_failure", [False, True])
async def test_original_table_followup_reuses_user_paper_and_reaches_current_pdf(
    client, db_session, offline_agent, repeat_after_failure,
):
    model, zotero, captured = offline_agent
    original_answer = model.side_effect

    async def answer_requested_two_sections(**kwargs):
        # This test exercises locator continuity, not format-repair failures.
        # The former one-sentence stub violates the user's explicit two-sentence
        # request and correctly spends the shared repair budget in the new API.
        result = await original_answer(**kwargs)
        return replace(result, content=(
            "原文信息：RM70% 与 CM70% 对应随机缺失和连续缺失。[S1]\n"
            "教学解释：两者区别在于缺失位置随机分布还是时间上连续。[S2]"
        ))

    model.side_effect = answer_requested_two_sections
    await seed(db_session)
    history = list(STD_DATASET_HISTORY)
    if repeat_after_failure:
        history.extend([
            {"role": "user", "content": STD_TABLE_FOLLOWUP},
            {"role": "assistant", "content": "尚未在当前资料范围定位到指定论文，无法核对所问原文或页表。"},
        ])
    response = await client.post("/api/v1/agent/chat", json={
        "question": STD_TABLE_FOLLOWUP, "history": history,
        "knowledge_category": SCOPE,
    })
    body = response.json()
    assert response.status_code == 200
    assert body["inference_mode"] == "model" and body["total_tokens"] == 20
    assert body["verification_status"] == "verified" and not body["output_issues"]
    assert body["output_status"] == ("not_checked" if repeat_after_failure else "passed")
    paper_citations = [citation for citation in body["citations"] if citation["source"] == "paper"]
    assert paper_citations and {citation["item_id"] for citation in paper_citations} == {"std"}
    assert {citation["page"] for citation in paper_citations} == {8}
    evidence = captured[0][-1]["content"].split("可引用材料：", 1)[1].split("[END_CURRENT_EVIDENCE]", 1)[0]
    assert "TABLE2_RAW" in evidence and "PAGE8_CONTEXT" in evidence
    assert "GAT_PAGE2" not in evidence and "PRIVATE_ONLY" not in evidence and "OTHER_PAGE" not in evidence
    assert "PEMS08" not in evidence  # The previous assistant answer is not PDF evidence.
    if not repeat_after_failure:
        # A forbidden historical value is a user output requirement, not PDF
        # evidence; its location must stay outside the explicit source boundary.
        assert "PEMS08" in captured[0][-1]["content"].split("[END_CURRENT_EVIDENCE]", 1)[1]
    assert STD_TABLE_FOLLOWUP in captured[0][-1]["content"]
    assert any(step["tool"] == "contextual_query" for step in body["tool_steps"])
    model.assert_awaited_once()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("history,category", [
    ([], SCOPE),
    ([STD_DATASET_HISTORY[1]], SCOPE),
    (STD_DATASET_HISTORY, "另一个分类"),
    (STD_DATASET_HISTORY, "没有这个分类"),
])
async def test_table_followup_requires_user_topic_and_current_category_permission(
    client, db_session, offline_agent, history, category,
):
    model, zotero, _ = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={
        "question": STD_TABLE_FOLLOWUP, "history": history, "knowledge_category": category,
    })
    body = response.json()
    assert response.status_code == 200
    assert body["citations"] == [] and body["inference_mode"] == "none"
    model.assert_not_awaited()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_named_table_target_overrides_previous_user_paper(
    client, db_session, offline_agent,
):
    _, _, captured = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={
        "question": "这篇论文的 GATGPT Table 2 中给出什么结果？",
        "history": STD_DATASET_HISTORY, "knowledge_category": SCOPE,
    })
    assert response.status_code == 200
    assert {citation["item_id"] for citation in response.json()["citations"] if citation["source"] == "paper"} == {"gat"}
    evidence = captured[0][-1]["content"].split("可引用材料：", 1)[1]
    assert "GAT_PAGE2" in evidence and "TABLE2_RAW" not in evidence
    assert not any(step["tool"] == "contextual_query" for step in response.json()["tool_steps"])


@pytest.mark.asyncio
async def test_new_unavailable_explicit_title_never_reverts_to_previous_paper(
    client, db_session, offline_agent,
):
    model, zotero, _ = offline_agent
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={
        "question": "这篇论文的《AbsentGPT: A New Unavailable Paper》第8页Table2说了什么？",
        "history": STD_DATASET_HISTORY, "knowledge_category": SCOPE,
    })
    body = response.json()
    assert response.status_code == 200
    assert body["citations"] == [] and body["inference_mode"] == "none"
    model.assert_not_awaited()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
async def test_chinese_protocol_comparison_selects_english_protocols_from_both_pdfs(client, db_session, offline_agent):
    _, _, captured = offline_agent
    for pid, name in (("alpha", "AlphaModel"), ("beta", "BetaModel")):
        db_session.add(PaperEntity(id=pid, title=f"{name}: A Method for Imputation", source="test"))
    await db_session.flush()
    for pid in ("alpha", "beta"):
        db_session.add(KnowledgeBase(id=f"{pid}-note", title="Note", category=SCOPE,
                                    source_paper_id=pid, content="数据集缺失设置指标 NOTE_FAKE"))
        db_session.add_all(chunk(pid, index, "imputation model approach framework SUMMARY_NO_PROTOCOL", page=1)
                           for index in range(12))
        db_session.add_all([
            chunk(pid, 20, f"Datasets: {pid}Data measures speed. DATASET_{pid}", page=5),
            chunk(pid, 21, f"Experimental settings: missing random block masking, training validation test split. MASK_{pid}", page=6),
            chunk(pid, 22, f"Evaluation metrics: imputation MAE RMSE MSE MAPE, forecasting is evaluated separately. METRIC_{pid}", page=8),
        ])
    await db_session.commit()
    response = await client.post("/api/v1/agent/chat", json={
        "question": "对比AlphaModel和BetaModel的实验协议，分别说明插补的数据集、缺失设置与评价指标，并把预测实验分开。",
        "knowledge_category": SCOPE,
    })
    assert response.status_code == 200
    citations = response.json()["citations"]
    assert len(citations) <= 6 and all(c["source"] == "paper" for c in citations)
    assert {c["item_id"] for c in citations} == {"alpha", "beta"}
    assert all(sum(c["item_id"] == pid for c in citations) == 3 for pid in ("alpha", "beta"))
    prompt = captured[0][-1]["content"]
    assert "SUMMARY_NO_PROTOCOL" not in prompt and "NOTE_FAKE" not in prompt
    assert all(f"{kind}_{pid}" in prompt for kind in ("DATASET", "MASK", "METRIC") for pid in ("alpha", "beta"))
    assert any(step["tool"] == "query_expansion" for step in response.json()["tool_steps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["STD-PLM第8页Table2", "STD-PLM的方法是什么？", "比较STD-PLM的不同方法"])
async def test_shared_prefix_requires_an_unambiguous_title(client, db_session, offline_agent, question):
    model, zotero, _ = offline_agent
    await seed(db_session)
    db_session.add(PaperEntity(id="other-std", title="STD-PLM: Another Unrelated Method", source="test"))
    await db_session.flush()
    db_session.add_all([
        KnowledgeBase(id="other-std-note", title="Other", category=SCOPE, source_paper_id="other-std", content="Other"),
        chunk("other-std", 0, "Table2: WRONG_PDF_VALUES", page=8),
    ])
    await db_session.commit()
    response = await client.post("/api/v1/agent/chat", json={"question": question, "knowledge_category": SCOPE})
    body = response.json()
    assert response.status_code == 200
    assert body["citations"] == [] and body["inference_mode"] == "none"
    assert "完整题名" in body["answer"]
    model.assert_not_awaited()
    zotero.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_title_uses_only_the_intended_pdf_with_shared_prefix(client, db_session, offline_agent):
    _, _, captured = offline_agent
    await seed(db_session)
    db_session.add(PaperEntity(id="other-std", title="STD-PLM: Another Unrelated Method", source="test"))
    await db_session.flush()
    db_session.add_all([
        KnowledgeBase(id="other-std-note", title="Other", category=SCOPE, source_paper_id="other-std", content="Other"),
        chunk("other-std", 0, "Table2: WRONG_PDF_VALUES", page=8),
    ])
    await db_session.commit()
    response = await client.post("/api/v1/agent/chat", json={
        "question": f"{STD_TITLE} 第8页Table2", "knowledge_category": SCOPE,
    })
    assert response.status_code == 200
    assert "TABLE2_RAW" in captured[0][-1]["content"]
    assert "WRONG_PDF_VALUES" not in captured[0][-1]["content"]
    assert {c["item_id"] for c in response.json()["citations"] if c["source"] == "paper"} == {"std"}


@pytest.mark.asyncio
async def test_generic_fair_candidate_pool_preserves_old_documents_and_late_relevant_chunks(db_session):
    db_session.add_all(PaperEntity(id=pid, title=f"Document {pid}", source="test") for pid in ("old", "new"))
    await db_session.flush()
    db_session.add_all(chunk(pid, i, "buried_unique_signal" if pid == "old" and i == 699 else "ordinary data", year=year)
                       for pid, year in [("old", 2020), ("new", 2026)] for i in range(700))
    await db_session.commit()
    coverage = {}
    pool = await agent._paper_candidates(db_session, query="buried_unique_signal", coverage=coverage)
    assert len(pool) == 600 and coverage["scope_chunks"] == 1400
    assert {c.metadata["paper_id"] for c in pool} == {"old", "new"}
    assert "old-699" in {c.chunk_id for c in pool}
    assert sum(c.metadata["paper_id"] == "old" for c in pool) == 300
