"""Research planning must see the selected note's later sections, offline."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1 import knowledge
from app.models.knowledge import KnowledgeBase
from app.schemas.knowledge import AIAnalyzeRequest
from app.services.features.reading_notes import render_reading_note
from app.services.inference import AllModelsUnavailableError


@pytest.mark.asyncio
async def test_research_analysis_uses_late_experiment_notes_and_reports_coverage(db_session, monkeypatch):
    card = {"schema_version": 1, "status": "reading", "sections": {
        "overview": {"paper_notes": "只介绍研究背景。" * 2000},
        "experiments": {
            "paper_notes": "DELAYED_EXPERIMENT_CASE：先按时间切分，仅在人工遮蔽的真值处评价。",
            "ai_suggestions": "尚未运行的AI建议：比较前值与线性插补。",
            "user_notes": "我的约束：只能使用训练集估计归一化参数。",
        },
    }}
    db_session.add(KnowledgeBase(
        id="late-note", title="交通流阅读笔记", category="交通流缺失修复",
        card_type="reading_note", card_data=card, content=render_reading_note(card),
        research_points=[], tags=[],
    ))
    await db_session.commit()
    captured = []

    async def model(**kwargs):
        assert not db_session.in_transaction()
        captured.append(kwargs["messages"][-1]["content"])
        return SimpleNamespace(
            content="待运行的实验计划", profile={"provider": "mock", "model": "mock"},
            fallback_used=False, usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    monkeypatch.setattr(knowledge, "chat_with_fallback", model)
    judge = AsyncMock(return_value=None)
    monkeypatch.setattr(knowledge, "judge_architecture", judge)
    result = await knowledge.ai_analyze_research(AIAnalyzeRequest(
        knowledge_ids=["late-note"], query="设计实验，明确评价掩码和训练集归一化约束",
    ), db_session)
    assert "DELAYED_EXPERIMENT_CASE" in captured[0]
    assert "AI建议，非论文事实" in captured[0]
    assert "个人想法，非论文事实" in captured[0]
    assert "尚未运行的AI建议" in captured[0]
    assert result.coverage and "18000" in result.coverage
    assert result.warnings
    assert len(captured) == 1  # Selection does not add another generation call.
    assert "DELAYED_EXPERIMENT_CASE" in judge.await_args.args[0]


@pytest.mark.asyncio
async def test_offline_research_analysis_still_reports_bounded_material(db_session, monkeypatch):
    db_session.add(KnowledgeBase(
        id="old-note", title="旧版笔记", category="交通流", content="尚未运行的真实实验想法",
        research_points=[], tags=[],
    ))
    await db_session.commit()
    call = AsyncMock(side_effect=AllModelsUnavailableError([], {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    }))
    monkeypatch.setattr(knowledge, "chat_with_fallback", call)
    judge = AsyncMock()
    monkeypatch.setattr(knowledge, "judge_architecture", judge)
    result = await knowledge.ai_analyze_research(AIAnalyzeRequest(knowledge_ids=["old-note"]), db_session)
    assert not result.model_completed
    assert result.coverage and "知识库正文" in result.coverage
    assert result.warnings
    assert not db_session.in_transaction()
    call.assert_awaited_once()
    judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_material_does_not_spend_model_request(db_session, monkeypatch):
    db_session.add(KnowledgeBase(
        id="empty", title="还没有正文", category="交通流", content="", research_points=[], tags=[],
    ))
    await db_session.commit()
    call = AsyncMock()
    monkeypatch.setattr(knowledge, "chat_with_fallback", call)
    with pytest.raises(HTTPException) as raised:
        await knowledge.ai_analyze_research(AIAnalyzeRequest(knowledge_ids=["empty"]), db_session)
    assert raised.value.status_code == 422
    assert not db_session.in_transaction()
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_ids_do_not_inflate_evidence_and_missing_ids_are_reported(db_session, monkeypatch):
    db_session.add(KnowledgeBase(
        id="note", title="我的条目", category="交通流", content="真实约束", research_points=[], tags=[],
    ))
    await db_session.commit()
    call = AsyncMock(return_value=SimpleNamespace(
        content="未运行计划", profile={"provider": "mock", "model": "mock"}, fallback_used=False,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    ))
    monkeypatch.setattr(knowledge, "chat_with_fallback", call)
    monkeypatch.setattr(knowledge, "judge_architecture", AsyncMock(return_value=None))
    result = await knowledge.ai_analyze_research(AIAnalyzeRequest(
        knowledge_ids=["note", "note", "missing"],
    ), db_session)
    assert result.knowledge_count == 1
    assert any("已不存在" in warning for warning in result.warnings)
    assert call.await_args.kwargs["messages"][-1]["content"].count("ID=note") == 1
