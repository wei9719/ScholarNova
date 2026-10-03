"""Follow-up retrieval uses user topic context, never historical answer evidence."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import agent
from app.models.knowledge import KnowledgeBase
from app.services.inference.model_router import ModelAttempt, RoutedChatResult

QUESTION = "把刚才的限制变成两项后续验证任务，不要重述整段回答。"
ANCHOR = "缓存延迟资料的结论和限制是什么？"
HISTORY = [
    {"role": "user", "content": ANCHOR},
    {"role": "assistant", "content": "HISTORICAL_UNSUPPORTED_ASSERTION 真实准确率99%。[S99]"},
]


@pytest.fixture
def offline_retrieval(monkeypatch):
    monkeypatch.setattr(agent, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "get_model_for_task", lambda _task: {"provider": "fake", "model": "offline"})
    monkeypatch.setattr("app.services.retrieval.hybrid.get_embedding_config", lambda: {"enabled": False})
    zotero = MagicMock(side_effect=AssertionError("Zotero must remain disabled"))
    monkeypatch.setattr(agent, "ZoteroLocalClient", zotero)
    usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20, "requests": 1}
    model = AsyncMock(return_value=RoutedChatResult(
        content="缓存延迟材料仅包含合成请求，不能证明真实用户的表现。[S1]",
        profile={"provider": "fake", "model": "offline"}, usage=usage, fallback_used=False,
        attempts=(ModelAttempt(role="primary", provider="fake", model="offline", status="completed", **usage),),
    ))
    monkeypatch.setattr(agent, "chat_with_fallback", model)
    ranking = AsyncMock(wraps=agent.rank_chunks_hybrid)
    monkeypatch.setattr(agent, "rank_chunks_hybrid", ranking)
    return model, ranking, zotero


async def seed(db):
    db.add_all([
        KnowledgeBase(id="cache-note", title="缓存延迟试验", category="缓存",
                      content="基线耗时为100毫秒，优化后耗时为60毫秒。样本仅含合成请求。CACHE_ONLY。"),
        KnowledgeBase(id="traffic-note", title="缓存延迟在交通中的无关记录", category="交通",
                      content="缓存延迟在交通中的干扰条目。TRAFFIC_ONLY。"),
    ])
    await db.commit()


@pytest.mark.asyncio
async def test_live_research_followup_retrieves_same_scope_and_reaches_model(client, db_session, offline_retrieval):
    model, ranking, zotero = offline_retrieval
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={
        "question": QUESTION, "history": HISTORY, "use_zotero": False, "knowledge_category": "缓存",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["response_type"] == "research" and data["inference_mode"] == "model"
    assert data["model_attempts"] and data["total_tokens"] == 20
    assert {citation["item_id"] for citation in data["citations"]} == {"cache-note"}
    model.assert_awaited_once()
    ranking.assert_awaited_once()
    retrieval_query = ranking.await_args.args[1]
    assert QUESTION in retrieval_query and ANCHOR in retrieval_query
    assert "HISTORICAL_UNSUPPORTED_ASSERTION" not in retrieval_query and "99%" not in retrieval_query
    prompt = model.await_args.kwargs["messages"]
    evidence = prompt[-1]["content"].split("可引用材料：", 1)[1]
    assert "CACHE_ONLY" in evidence and "TRAFFIC_ONLY" not in evidence
    assert "HISTORICAL_UNSUPPORTED_ASSERTION" not in evidence
    assert QUESTION in prompt[-1]["content"]
    assert any(step["tool"] == "contextual_query" for step in data["tool_steps"])
    zotero.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("history,question,category", [
    ([], QUESTION, "缓存"),
    ([HISTORY[1]], QUESTION, "缓存"),
    (HISTORY, "现在讨论蛋白质折叠结构。", "缓存"),
    (HISTORY, "刚才的限制先不讨论，换个话题：蛋白质折叠结构是什么？", "缓存"),
    (HISTORY, QUESTION, "不存在"),
])
async def test_followup_does_not_expand_to_old_answers_other_topics_or_categories(
    client, db_session, offline_retrieval, history, question, category,
):
    model, ranking, zotero = offline_retrieval
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={
        "question": question, "history": history, "use_zotero": False, "knowledge_category": category,
    })
    data = response.json()
    assert response.status_code == 200
    assert data["citations"] == [] and data["inference_mode"] == "none"
    model.assert_not_awaited()
    zotero.assert_not_called()
    if category != "不存在":
        assert ranking.await_args.args[1] == question


@pytest.mark.parametrize("middle_question", ["刚才的方法有哪些局限？", "下一步怎么做？"])
def test_multiple_followups_keep_nearest_user_topic_and_bound_history(middle_question):
    anchor = "缓存延迟" * 200
    history = [agent.AgentMessage(**item) for item in [
        {"role": "user", "content": "旧研究主题不应使用"},
        {"role": "user", "content": anchor},
        HISTORY[1],
        {"role": "user", "content": middle_question},
        HISTORY[1],
    ]]
    query = agent._contextual_retrieval_query(QUESTION, history)
    assert query == f"{anchor[:600]}\n{QUESTION}"
    assert "旧研究主题" not in query and "HISTORICAL_UNSUPPORTED_ASSERTION" not in query


def test_help_topic_breaks_older_research_context():
    history = [agent.AgentMessage(**item) for item in [*HISTORY,
        {"role": "user", "content": "我该如何使用你？"},
        {"role": "assistant", "content": "先准备论文。"},
    ]]
    assert agent._contextual_retrieval_query(QUESTION, history) == QUESTION


@pytest.mark.asyncio
async def test_research_followup_respects_disabled_sources(client, db_session, offline_retrieval):
    model, ranking, zotero = offline_retrieval
    await seed(db_session)
    response = await client.post("/api/v1/agent/chat", json={
        "question": QUESTION, "history": HISTORY,
        "use_knowledge": False, "use_zotero": False, "knowledge_category": "缓存",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["citations"] == [] and data["inference_mode"] == "none"
    assert ranking.await_args.args[2] == []
    model.assert_not_awaited()
    zotero.assert_not_called()
