"""Answer requirements must share, not expand, the citation-repair budget."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import agent
from app.models.knowledge import KnowledgeBase
from app.services.inference.model_router import AllModelsUnavailableError, ModelAttempt, RoutedChatResult


PROFILE = {"provider": "test", "model": "fake"}
USAGE = {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40, "requests": 1}
QUESTION = "随机缺失与连续缺失分别表示什么？只回答两句，区分原文与教学，不要重复上一问的数据集。"
HISTORY = [
    {"role": "user", "content": "插补实验使用哪个数据集？"},
    {"role": "assistant", "content": "插补实验采用 SensorBench-08 数据集。[S1]"},
]
GOOD = ("原文信息：随机缺失将观测点随机遮挡。[S1]\n"
        "教学解释：连续缺失将一段连续观测同时遮挡。[S1]")
REPEATED = ("原文信息：随机缺失将观测点随机遮挡。[S1]\n"
            "教学解释：SensorBench-08 的连续缺失将连续观测同时遮挡。[S1]")


def completed(content):
    return RoutedChatResult(
        content=content, profile=PROFILE, usage=USAGE.copy(), fallback_used=False,
        attempts=(ModelAttempt(role="primary", provider="test", model="fake", status="completed", **USAGE),),
    )


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(agent, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "get_model_for_task", lambda _task: PROFILE)
    monkeypatch.setattr("app.services.retrieval.hybrid.get_embedding_config", lambda: {"enabled": False})


async def ask(client, db_session, *, question=QUESTION, history=None):
    db_session.add(KnowledgeBase(
        title="缺失插补实验", category="OutputTests",
        content="随机缺失按点采样遮挡观测。连续缺失按时间段遮挡观测。SensorBench-08 为实验数据集。",
    ))
    await db_session.commit()
    response = await client.post("/api/v1/agent/chat", json={
        "question": question, "history": HISTORY if history is None else history,
        "knowledge_category": "OutputTests", "use_zotero": False,
    })
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_citation_valid_but_repeated_answer_uses_single_bounded_repair(client, db_session, monkeypatch):
    history = deepcopy(HISTORY)
    routed = AsyncMock(side_effect=[completed(REPEATED), completed(GOOD)])
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session, history=history)
    assert routed.await_count == 2
    assert body["answer"] == GOOD
    assert body["output_status"] == "passed" and body["output_issues"] == []
    assert body["verification_status"] == "verified" and body["total_tokens"] == 80
    assert body["inference_mode"] == "model"
    repair = routed.await_args_list[1].kwargs
    assert repair["allow_fallback"] is False and repair["timeout_seconds"] == 10
    assert "SensorBench-08" in repair["messages"][-1]["content"]
    assert history == HISTORY  # Never rewrite actual stored/request history.


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_failed_format_is_visible_without_truncating_or_inventing_an_answer(client, db_session, monkeypatch):
    routed = AsyncMock(return_value=completed(REPEATED))
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session)
    assert routed.await_count == 2  # No third request or recursive repair.
    assert body["answer"] == REPEATED
    assert body["verification_status"] == "verified"
    assert body["output_status"] == "failed" and body["output_issues"]
    assert body["inference_mode"] == "model" and body["fallback_used"] is False
    step = next(step for step in body["tool_steps"] if step["tool"] == "answer_format_verification")
    assert step["status"] == "unavailable" and "未满足" in step["detail"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_citation_and_sentence_failures_share_one_repair(client, db_session, monkeypatch):
    draft = GOOD + "\n随机遮挡需要固定掩码。"
    routed = AsyncMock(side_effect=[completed(draft), completed(GOOD)])
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session)
    assert routed.await_count == 2 and body["answer"] == GOOD
    assert body["output_status"] == "passed" and body["verification_status"] == "verified"
    assert len([step for step in body["tool_steps"] if step["tool"] == "answer_repair"]) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_format_repair_does_not_bypass_invalid_citation_fallback(client, db_session, monkeypatch):
    routed = AsyncMock(side_effect=[completed(REPEATED), completed(GOOD.replace("S1", "S999"))])
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session)
    assert routed.await_count == 2
    assert body["inference_mode"] == "deterministic_fallback"
    assert body["fallback_reason"] == "citation_verification"
    assert "S999" not in body["answer"]
    assert body["output_status"] == "failed"  # Evidence excerpts are not the requested two-sentence answer.


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_current_question_can_explicitly_reuse_identifier_without_blanket_history_filter(client, db_session, monkeypatch):
    answer = "原文信息：SensorBench-08 的随机缺失按点采样。[S1]\n教学解释：连续缺失按时间段遮挡。[S1]"
    routed = AsyncMock(return_value=completed(answer))
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session, question="SensorBench-08 的随机缺失与连续缺失是什么？只回答两句，区分原文与教学。")
    assert routed.await_count == 1 and body["answer"] == answer
    assert body["output_status"] == "passed"


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_unconstrained_answer_does_not_gain_an_extra_model_call(client, db_session, monkeypatch):
    routed = AsyncMock(return_value=completed("随机缺失将观测点随机遮挡。[S1]"))
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session, question="随机缺失表示什么？")
    assert routed.await_count == 1 and body["output_status"] == "not_applicable"


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_format_repair_timeout_preserves_valid_original_and_reports_attempt_usage(client, db_session, monkeypatch):
    attempt = ModelAttempt(role="primary", provider="test", model="fake", status="unavailable", error_type="TimeoutError",
                           request_attempts=1, responses_received=0, usage_reports=0)
    unavailable = AllModelsUnavailableError([attempt], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    routed = AsyncMock(side_effect=[completed(REPEATED), unavailable])
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session)
    assert routed.await_count == 2 and body["answer"] == REPEATED
    assert body["output_status"] == "failed" and body["verification_status"] == "verified"
    assert body["inference_mode"] == "model" and body["fallback_reason"] is None
    assert body["total_tokens"] == 40 and body["model_attempts"][-1]["error_type"] == "TimeoutError"
    repair = next(step for step in body["tool_steps"] if step["tool"] == "answer_repair")
    assert repair["status"] == "unavailable" and "保留实际回答" in repair["detail"]
    assert "未报告用量不代表免费" in repair["detail"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_unknown_repeat_object_does_not_trigger_speculative_model_repair(client, db_session, monkeypatch):
    routed = AsyncMock(return_value=completed("随机缺失将观测点随机遮挡。[S1]"))
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session, question="随机缺失是什么意思？不要重复上一问的原因。")
    assert routed.await_count == 1
    assert body["output_status"] == "not_checked" and body["output_unchecked"]
    assert not body["output_issues"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
@pytest.mark.parametrize("question,answer,status", [
    ("随机缺失是什么？不要再只回答两句，请详细解释。", "随机缺失将观测点随机遮挡。[S1]", "not_applicable"),
    ("随机缺失是什么？只回答25句。", "随机缺失将观测点随机遮挡。[S1]", "not_checked"),
    ("随机缺失是什么？只回答两句。", "随机缺失将观测点随机遮挡 [S1]\n连续缺失将时段连续遮挡 [S1]", "not_checked"),
])
async def test_uncheckable_or_negated_counts_do_not_spend_a_format_repair(client, db_session, monkeypatch, question, answer, status):
    routed = AsyncMock(return_value=completed(answer))
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    body = await ask(client, db_session, question=question)
    assert routed.await_count == 1 and body["answer"] == answer
    assert body["verification_status"] == "verified" and body["output_status"] == status
    assert not body["output_issues"]
    assert bool(body["output_unchecked"]) == (status == "not_checked")
