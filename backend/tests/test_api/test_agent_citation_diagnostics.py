"""Research structure, strict citation boundaries and non-sensitive phase logs."""

import logging
import re
from time import perf_counter
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import agent
from app.models.knowledge import KnowledgeBase
from app.services.inference.model_router import (
    AllModelsUnavailableError,
    ModelAttempt,
    RoutedChatResult,
)

QUESTION = "请按原文信息与教学解释分段回答检索问题，QUESTION_PRIVATE。"
ANSWER = ("原文信息（严格依据材料）\n论文材料强调来源编号需要保持可追溯。[S1]\n"
          "教学解释（非论文原文）\n材料支持按来源定位进行逐项核对，ANSWER_PRIVATE。[S1]")
USAGE = {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40, "requests": 1}
PROFILE = {"provider": "test", "model": "fake", "api_key": "APIKEY_PRIVATE", "base_url": "https://URL_PRIVATE"}


def completed(content):
    return RoutedChatResult(
        content=content, profile=PROFILE, usage=USAGE.copy(), fallback_used=False,
        attempts=(ModelAttempt(role="primary", provider="test", model="fake", status="completed", **USAGE),),
    )


@pytest.fixture
def local_evidence_only(monkeypatch):
    monkeypatch.setattr(agent, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "get_model_for_task", lambda _task: PROFILE)
    monkeypatch.setattr("app.services.retrieval.hybrid.get_embedding_config", lambda: {"enabled": False})


async def ask(client, db_session):
    db_session.add(KnowledgeBase(title="检索证据", category="CiteTests", content="检索要求保留来源编号以核对论文。SOURCE_PRIVATE"))
    await db_session.commit()
    response = await client.post("/api/v1/agent/chat", json={
        "question": QUESTION, "knowledge_category": "CiteTests", "use_zotero": False,
    })
    assert response.status_code == 200
    return response.json()


def diagnostic_text(caplog):
    text = "\n".join(record.getMessage() for record in caplog.records if record.name == agent.__name__)
    for private in ("QUESTION_PRIVATE", "SOURCE_PRIVATE", "ANSWER_PRIVATE", "APIKEY_PRIVATE", "URL_PRIVATE"):
        assert private not in text
    assert len(set(re.findall(r"trace=([a-f0-9]{12})", text))) == 1
    return text


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_evidence_only")
async def test_source_teaching_titles_do_not_trigger_a_second_model_request(client, db_session, monkeypatch, caplog):
    routed = AsyncMock(return_value=completed(ANSWER))
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    with caplog.at_level(logging.INFO, logger=agent.__name__):
        body = await ask(client, db_session)
    assert body["verification_status"] == "verified" and body["inference_mode"] == "model"
    assert body["citation_coverage"] == 1 and body["total_tokens"] == 40
    assert not any(step["tool"] == "answer_repair" for step in body["tool_steps"])
    routed.assert_awaited_once()
    messages = routed.await_args.kwargs["messages"]
    assert "优先遵循用户指定" in messages[0]["content"]
    assert "没有指定结构时才" in messages[-1]["content"]
    assert "请先给出直接结论，再给出证据" not in messages[-1]["content"]
    text = diagnostic_text(caplog)
    assert "phase=generation status=completed" in text
    assert "phase=verification.initial status=verified" in text
    assert "claims=2 cited=2 uncited=0 invalid=0" in text
    assert "phase=finished status=model" in text


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_evidence_only")
@pytest.mark.parametrize("error_type", ["TimeoutError", "RateLimitError"])
async def test_repair_failure_exposes_only_error_type_and_keeps_original_attempts(client, db_session, monkeypatch, caplog, error_type):
    incomplete = ANSWER + "\n教学解释里的该项实验误差已经下降到14.36。"
    failed = ModelAttempt(role="primary", provider="test", model="fake", status="unavailable", error_type=error_type,
                          request_attempts=1, responses_received=0, usage_reports=0)
    unavailable = AllModelsUnavailableError([failed], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    routed = AsyncMock(side_effect=[completed(incomplete), unavailable])
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    with caplog.at_level(logging.INFO, logger=agent.__name__):
        body = await ask(client, db_session)
    assert routed.await_count == 2
    repair_request = routed.await_args_list[1].kwargs
    assert repair_request["timeout_seconds"] == 10 and repair_request["allow_fallback"] is False
    assert repair_request["max_tokens"] == 900
    assert body["inference_mode"] == "deterministic_fallback" and body["fallback_reason"] == "citation_verification"
    assert body["model_attempts"][-1]["error_type"] == error_type
    assert body["model_attempts"][-1]["request_attempts"] == 1
    assert body["total_tokens"] == 40
    repair = next(step for step in body["tool_steps"] if step["tool"] == "answer_repair")
    assert repair["status"] == "unavailable" and error_type in repair["detail"]
    assert "等待预算 10 秒" in repair["detail"] and "未报告用量不代表免费" in repair["detail"]
    text = diagnostic_text(caplog)
    assert "phase=verification.initial status=partial" in text and "uncited=1" in text
    assert f"errors={error_type}" in text and "budget_s=10.0" in text
    assert "phase=repair status=unavailable" in text and "phase=verification.fallback status=verified" in text


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_evidence_only")
async def test_single_repair_preserves_requested_structure_and_logs_reverification(client, db_session, monkeypatch, caplog):
    routed = AsyncMock(side_effect=[completed(ANSWER + "\n未带来源的教学事实需要核对。"), completed(ANSWER)])
    monkeypatch.setattr(agent, "chat_with_fallback", routed)
    with caplog.at_level(logging.INFO, logger=agent.__name__):
        body = await ask(client, db_session)
    assert routed.await_count == 2 and body["verification_status"] == "verified"
    assert body["inference_mode"] == "model" and body["total_tokens"] == 80
    repair_prompt = routed.await_args_list[1].kwargs["messages"][0]["content"]
    assert "保留用户明确要求" in repair_prompt and "事实、数字与来源声明同样必须引用" in repair_prompt
    text = diagnostic_text(caplog)
    assert "phase=repair status=completed" in text
    assert "phase=verification.repaired status=verified" in text


def test_diagnostic_error_fields_cannot_log_arbitrary_messages(caplog):
    with caplog.at_level(logging.INFO, logger=agent.__name__):
        for attempts in ([{"error_type": "TimeoutError api_key=DO_NOT_LOG_SECRET"}], []):
            agent._log_answer_phase("abcdef123456", "repair", perf_counter(), status="unavailable", attempts=attempts)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert text.count("errors=UnknownError") == 2 and "DO_NOT_LOG_SECRET" not in text
