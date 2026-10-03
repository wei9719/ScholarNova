"""Offline regressions for contextual, bounded product-help answers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.agent import (
    AgentMessage,
    _is_product_help,
    _product_help_answer,
    _product_help_model_context,
)
from app.services.inference.model_router import (
    AllModelsUnavailableError,
    ModelAttempt,
    RoutedChatResult,
)


HELP_PROFILE = {
    "provider": "siliconflow",
    "model": "Qwen/offline-help-model",
    "api_key": "offline-help-secret-must-not-appear-in-prompts",
    "base_url": "http://127.0.0.1:9/v1",
}
HELP_USAGE = {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100, "requests": 1}
FOLLOWUPS = ["那下一步呢", "那怎么用呢", "然后呢", "具体一点", "what next",
             "这两次回答咋一样", "你一直重复回答", "我还是没听懂", "换种说法",
             "你是不是没有调用AI", "why are the answers the same"]


def history_of(*questions):
    return [
        message
        for question in questions
        for message in (
            AgentMessage(role="user", content=question),
            AgentMessage(role="assistant", content="这是上一轮回答。"),
        )
    ]


@pytest.mark.parametrize("question", FOLLOWUPS)
@pytest.mark.parametrize(
    "history,expected",
    [
        ([], False),
        ([AgentMessage(role="assistant", content="我可以介绍 ScholarNova 的用法。")], False),
        (history_of("我该如何使用你"), True),
        (history_of("我该如何使用你", "那下一步呢"), True),
        (history_of("什么是RAG"), False),
        (history_of("我该如何使用你", "什么是RAG"), False),
        (history_of("我该如何使用你", "什么是RAG", "然后呢"), False),
        (history_of("什么是RAG", "我该如何使用你"), True),
    ],
    ids=["empty", "assistant-only", "help", "help-chain", "research",
         "research-interruption", "interrupted-chain", "back-to-help"],
)
def test_help_followups_require_traceable_uninterrupted_user_context(question, history, expected):
    assert _is_product_help(question, history) is expected


@pytest.mark.parametrize(
    "question",
    ["分析这篇论文的方法", "论文中智能体能做什么", "什么是RAG",
     "Who are you studying in this paper?", "这篇论文下一步实验该怎么做"],
)
def test_explicit_research_overrides_a_product_help_chain(question):
    assert _is_product_help(question, history_of("我该如何使用你", "那下一步呢")) is False


@pytest.mark.parametrize("question", ["你好", "您好"])
def test_standalone_chinese_greetings_still_enter_product_help(question):
    assert _is_product_help(question) is True


@pytest.mark.parametrize("question", [
    "准备论文", "导入论文", "导入PDF", "选择来源", "开启来源", "提出科研问题",
    "输入问题", "配置模型", "配置API", "我卡在选择来源这一步", "我已经导入论文了", "还没导入PDF",
])
@pytest.mark.parametrize("history,expected", [
    ([], False),
    ([AgentMessage(role="assistant", content="你卡在准备论文、选择来源，还是提出科研问题这一步？")], False),
    (history_of("我该如何使用你", "那下一步呢"), True),
    (history_of("我该如何使用你", "什么是RAG"), False),
], ids=["empty", "assistant-only", "help-chain", "research-interruption"])
def test_progress_stage_reply_requires_real_user_help_context(question, history, expected):
    assert _is_product_help(question, history) is expected


@pytest.fixture
def isolated_help(monkeypatch):
    monkeypatch.setattr("app.api.v1.agent.check_rate_limit", lambda *args, **kwargs: None)
    profile_lookup = MagicMock(return_value=dict(HELP_PROFILE))
    monkeypatch.setattr("app.api.v1.agent.get_model_for_task", profile_lookup)
    blocked = []
    for name in ("_knowledge_candidates", "_paper_candidates", "rank_chunks_hybrid"):
        mock = AsyncMock(side_effect=AssertionError(f"Help must not perform {name}"))
        monkeypatch.setattr(f"app.api.v1.agent.{name}", mock)
        blocked.append(mock)
    for name in ("ZoteroLocalClient", "LLMGateway"):
        mock = MagicMock(side_effect=AssertionError(f"Real service forbidden: {name}"))
        monkeypatch.setattr(f"app.api.v1.agent.{name}", mock)
        blocked.append(mock)
    chat = AsyncMock(side_effect=AssertionError("Configure an offline model result explicitly"))
    monkeypatch.setattr("app.api.v1.agent.chat_with_fallback", chat)
    yield chat, profile_lookup
    for mock in blocked:
        mock.assert_not_called()


def model_attempt(status="completed", error_type=None):
    return ModelAttempt(
        role="primary", provider=HELP_PROFILE["provider"], model=HELP_PROFILE["model"],
        status=status, error_type=error_type, **HELP_USAGE,
    )


def model_result(content):
    return RoutedChatResult(
        content=content, profile=dict(HELP_PROFILE), usage=dict(HELP_USAGE),
        attempts=(model_attempt(),), fallback_used=False,
    )


def assert_product_help_metadata(data):
    assert data["response_type"] == "product_help"
    assert data["grounded"] is False
    assert data["citations"] == []
    assert data["verification_status"] == "not_applicable"
    assert data["retrieval_tokens"] == 0
    assert data["model_fallback_used"] is False
    assert not ({"knowledge_search", "paper_fulltext_search", "zotero_search", "semantic_retrieval"}
                & {step["tool"] for step in data["tool_steps"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("use_knowledge,use_zotero", [(True, False), (False, True)])
async def test_model_help_is_bounded_uses_recent_history_and_reports_actual_usage(
    client, isolated_help, use_knowledge, use_zotero,
):
    chat, profile_lookup = isolated_help
    chat.side_effect = None
    chat.return_value = model_result("先选择材料来源，再提出具体问题；设置中可以核对 Zotero 连接状态。")
    history = [
        {"role": "user", "content": "OLD_USER_MUST_NOT_APPEAR"},
        {"role": "assistant", "content": "OLD_ASSISTANT_MUST_NOT_APPEAR"},
        *[
            {"role": "user" if index % 2 == 0 else "assistant",
             "content": f"RECENT_{index}_" + "字" * 1100 + f"TRUNCATED_TAIL_{index}"}
            for index in range(4)
        ],
    ]
    question = "我该如何使用你"
    response = await client.post("/api/v1/agent/chat", json={
        "question": question, "history": history,
        "use_knowledge": use_knowledge, "use_zotero": use_zotero,
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["answer"] == chat.return_value.content
    assert data["inference_mode"] == "model"
    assert data["model_route"] == "primary"
    assert data["fallback_used"] is False
    assert data["fallback_reason"] is None
    assert data["provider"] == HELP_PROFILE["provider"]
    assert data["model"] == HELP_PROFILE["model"]
    assert data["model_attempts"] == [model_attempt().to_dict()]
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert data[key] == HELP_USAGE[key]
    profile_lookup.assert_called_once_with("assistant")
    chat.assert_awaited_once()
    kwargs = chat.await_args.kwargs
    assert kwargs["task"] == "assistant"
    assert kwargs["allow_fallback"] is False
    assert kwargs["timeout_seconds"] == 60
    assert kwargs["max_tokens"] == 320
    assert kwargs["temperature"] == 0.2
    assert kwargs["profile"] == HELP_PROFILE
    messages = kwargs["messages"]
    assert messages[1:-1] == [
        {"role": message["role"], "content": message["content"][:1000]}
        for message in history[-4:]
    ]
    prompt = "\n".join(message["content"] for message in messages)
    assert question in messages[-1]["content"]
    assert _product_help_model_context(question) in prompt
    assert _product_help_answer(question) not in prompt
    assert "OLD_USER_MUST_NOT_APPEAR" not in prompt
    assert "OLD_ASSISTANT_MUST_NOT_APPEAR" not in prompt
    assert "TRUNCATED_TAIL_" not in prompt
    assert HELP_PROFILE["api_key"] not in prompt
    assert f"use_knowledge={str(use_knowledge).lower()}" in prompt
    assert f"use_zotero={str(use_zotero).lower()}" in prompt
    assert "未执行连接检测" in messages[0]["content"]
    assert HELP_PROFILE["api_key"] not in str(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "blank", "fake-citation"])
async def test_help_failure_returns_builtin_guide_without_losing_attempt_usage(
    client, isolated_help, failure,
):
    chat, _ = isolated_help
    if failure == "timeout":
        attempt = model_attempt(status="unavailable", error_type="TimeoutError")
        chat.side_effect = AllModelsUnavailableError([attempt], dict(HELP_USAGE))
        expected_reason = "model_unavailable"
    else:
        attempt = model_attempt()
        chat.side_effect = None
        chat.return_value = model_result(" \n\t " if failure == "blank" else "这款软件已经验证了论文结论。[S1]")
        expected_reason = "invalid_help_response"
    question = "我该如何使用你"
    response = await client.post("/api/v1/agent/chat", json={
        "question": question, "use_knowledge": True, "use_zotero": True,
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["answer"] != _product_help_answer(question)
    assert "本次 AI 使用指导未完成" in data["answer"]
    assert data["inference_mode"] == "deterministic_fallback"
    assert data["model_route"] == "deterministic"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == expected_reason
    assert data["model_attempts"] == [attempt.to_dict()]
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert data[key] == HELP_USAGE[key]
    chat.assert_awaited_once()
    assert chat.await_args.kwargs["allow_fallback"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("question", FOLLOWUPS)
async def test_contextual_help_without_key_uses_builtin_guide_without_any_model(
    client, isolated_help, question,
):
    chat, profile_lookup = isolated_help
    profile_lookup.return_value = {**HELP_PROFILE, "api_key": ""}
    response = await client.post("/api/v1/agent/chat", json={
        "question": question,
        "history": [message.model_dump() for message in history_of("我该如何使用你", "那下一步呢")],
        "use_knowledge": True, "use_zotero": True,
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["inference_mode"] == "deterministic_fallback"
    assert data["model_route"] == "deterministic"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "model_unavailable"
    assert data["model_attempts"] == []
    assert all(data[key] == 0 for key in (
        "prompt_tokens", "completion_tokens", "retrieval_tokens", "total_tokens"
    ))
    assert "ScholarNova" in data["answer"]
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_help_without_key_neither_reads_nor_writes_user_database(
    client, db_session, isolated_help, monkeypatch,
):
    chat, profile_lookup = isolated_help
    profile_lookup.return_value = {**HELP_PROFILE, "api_key": ""}
    database_calls = []
    for name in ("execute", "scalar", "scalars", "get", "delete", "flush", "commit"):
        mock = AsyncMock(side_effect=AssertionError(f"Help must not access user data: {name}"))
        monkeypatch.setattr(db_session, name, mock)
        database_calls.append(mock)
    for name in ("add", "add_all"):
        mock = MagicMock(side_effect=AssertionError(f"Help must not modify user data: {name}"))
        monkeypatch.setattr(db_session, name, mock)
        database_calls.append(mock)

    response = await client.post("/api/v1/agent/chat", json={
        "question": "我该如何使用你",
        "history": [{"role": "user", "content": "分析这篇论文的方法"}],
        "use_knowledge": True, "use_zotero": True,
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["inference_mode"] == "deterministic_fallback"
    assert data["fallback_reason"] == "model_unavailable"
    assert data["total_tokens"] == 0
    chat.assert_not_called()
    for mock in database_calls:
        mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_boundary", ["profile-lookup", "gateway-construction"])
async def test_help_configuration_errors_return_safe_guide_instead_of_500(
    client, isolated_help, monkeypatch, failure_boundary,
):
    from app.services.inference.model_router import chat_with_fallback

    chat, profile_lookup = isolated_help
    failure = ValueError(f"Invalid offline configuration: {HELP_PROFILE['api_key']}")
    factory = None
    if failure_boundary == "profile-lookup":
        profile_lookup.side_effect = failure
    else:
        # Exercise the real router's initialization boundary, but never create
        # a provider client or send a request, even if error handling regresses.
        factory = MagicMock()
        factory.from_profile.side_effect = failure
        monkeypatch.setattr("app.api.v1.agent.LLMGateway", factory)
        monkeypatch.setattr("app.api.v1.agent.chat_with_fallback", chat_with_fallback)
        monkeypatch.setattr(
            "app.services.inference.model_router.get_fallback_model_config",
            lambda: {"enabled": False},
        )

    question = "我该如何使用你"
    response = await client.post("/api/v1/agent/chat", json={
        "question": question, "use_knowledge": True, "use_zotero": True,
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["answer"] != _product_help_answer(question)
    assert "本次 AI 使用指导未完成" in data["answer"]
    assert data["inference_mode"] == "deterministic_fallback"
    assert data["model_route"] == "deterministic"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "model_unavailable"
    assert all(data[key] == 0 for key in (
        "prompt_tokens", "completion_tokens", "retrieval_tokens", "total_tokens"
    ))
    assert all(attempt["status"] == "unavailable" for attempt in data["model_attempts"])
    assert HELP_PROFILE["api_key"] not in str(data)
    profile_lookup.assert_called_once_with("assistant")
    chat.assert_not_called()
    if factory is not None:
        factory.from_profile.assert_called_once()
        factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("question,answer", [
    ("那下一步呢", "你目前卡在准备论文、选择来源，还是提出科研问题这一步？"),
    ("这两次回答咋一样", "你现在已经有一篇准备分析的论文了吗？"),
    ("what next", "Do you already have a paper ready to analyze?"),
])
async def test_feedback_uses_model_without_paper_retrieval_and_does_not_replay_guide(
    client, isolated_help, question, answer,
):
    chat, _ = isolated_help
    chat.side_effect = None
    chat.return_value = model_result(answer)
    response = await client.post("/api/v1/agent/chat", json={
        "question": question,
        "history": [message.model_dump() for message in history_of("我该如何使用你", "那下一步呢")],
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["inference_mode"] == "model"
    assert data["model_route"] == "primary"
    assert data["fallback_used"] is False
    assert data["answer"] == answer
    chat.assert_awaited_once()
    messages = chat.await_args.kwargs["messages"]
    assert messages[-1]["content"] == question
    prompt = messages[0]["content"]
    assert "用户已在 ScholarNova 智能体页面与你交谈，不要建议再次进入此页面" in prompt
    assert "本轮任务（优先于上面的通用步骤建议）：仅输出一个具体的进度澄清问句" in prompt
    assert "以问号结束，不附带操作步骤" in prompt
    assert "不要假设用户已了解或完成某步" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["你是不是没有调用AI", "你是不是没有用模型"])
async def test_model_call_status_feedback_does_not_force_a_progress_question(
    client, isolated_help, question,
):
    chat, _ = isolated_help
    answer = "仅凭聊天正文无法确认之前是否调用成功，请查看回答下方的模型尝试和用量状态。"
    chat.side_effect = None
    chat.return_value = model_result(answer)
    response = await client.post("/api/v1/agent/chat", json={
        "question": question,
        "history": [message.model_dump() for message in history_of("我该如何使用你", "那下一步呢")],
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["answer"] == answer
    assert data["inference_mode"] == "model"
    assert data["fallback_used"] is False
    assert data["fallback_reason"] is None
    chat.assert_awaited_once()
    prompt = chat.await_args.kwargs["messages"][0]["content"]
    assert "聊天正文不能证明之前模型是否调用成功" in prompt
    assert "本轮任务（优先于上面的通用步骤建议）" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("question,answer", [
    ("选择来源", "在当前页面按需开启 ScholarNova 知识库来源，再输入你要研究的问题。"),
    ("还没导入PDF", "先准备一份你有权使用的 PDF，通过应用的导入入口加入材料。"),
    ("我卡在配置模型", "在设置中填写模型提供商提供的配置并测试连接，不要在聊天中粘贴密钥。"),
])
async def test_progress_stage_reply_can_receive_steps_without_another_clarification(
    client, isolated_help, question, answer,
):
    chat, _ = isolated_help
    chat.side_effect = None
    chat.return_value = model_result(answer)
    response = await client.post("/api/v1/agent/chat", json={
        "question": question,
        "history": [
            {"role": "user", "content": "我该如何使用你"},
            {"role": "assistant", "content": "可以先准备论文、选择来源，再提具体问题。"},
            {"role": "user", "content": "那下一步呢"},
            {"role": "assistant", "content": "你卡在准备论文、选择来源，还是提出科研问题这一步？"},
        ],
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["answer"] == answer
    assert data["inference_mode"] == "model"
    assert data["model_route"] == "primary"
    assert data["fallback_used"] is False
    assert data["fallback_reason"] is None
    assert data["total_tokens"] == HELP_USAGE["total_tokens"]
    chat.assert_awaited_once()
    messages = chat.await_args.kwargs["messages"]
    assert messages[-1]["content"] == question
    assert "用户已在 ScholarNova 智能体页面与你交谈" in messages[0]["content"]
    assert "本轮任务（优先于上面的通用步骤建议）" not in messages[0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["那下一步呢", "这两次回答咋一样"])
@pytest.mark.parametrize("answer", [
    "你说得对，前文重复了。可以从搜索一篇论文开始，分析后保存重要结论，再提具体问题。",
    "1. 开启资料来源。2. 提出一个具体科研问题。",
    "你准备好了吗？接下来先选择来源，再提出一个问题。",
])
async def test_progress_followup_rejects_steps_and_preserves_reported_usage(
    client, isolated_help, question, answer,
):
    chat, _ = isolated_help
    usage = {
        "prompt_tokens": 800, "completion_tokens": 114, "total_tokens": 914,
        "requests": 1, "request_attempts": 1, "responses_received": 1, "usage_reports": 1,
    }
    attempt = ModelAttempt(
        role="primary", provider=HELP_PROFILE["provider"], model=HELP_PROFILE["model"],
        status="completed", **usage,
    )
    chat.side_effect = None
    chat.return_value = RoutedChatResult(
        content=answer, profile=dict(HELP_PROFILE), usage=usage,
        attempts=(attempt,), fallback_used=False,
    )
    response = await client.post("/api/v1/agent/chat", json={
        "question": question,
        "history": [
            {"role": "user", "content": "我该如何使用你"},
            {"role": "assistant", "content": "你可以打开资料来源开关，然后输入想研究的问题。"},
        ],
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["inference_mode"] == "deterministic_fallback"
    assert data["model_route"] == "deterministic"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "invalid_help_response"
    assert data["answer"] != answer
    assert "本次 AI 使用指导未完成" in data["answer"]
    assert len(data["answer"]) < 180
    assert "1. 准备材料" not in data["answer"]
    assert "开启资料来源" not in data["answer"]
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert data[key] == usage[key]
    assert data["model_attempts"] == [attempt.to_dict()]
    chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_model_answer_is_not_appended_again_and_usage_is_preserved(client, isolated_help):
    chat, _ = isolated_help
    previous = "先检索并分析一篇论文，再保存重要结论。"
    chat.side_effect = None
    chat.return_value = model_result(previous)
    response = await client.post("/api/v1/agent/chat", json={
        "question": "那下一步呢", "history": [
            {"role": "user", "content": "我该如何使用你"},
            {"role": "assistant", "content": previous},
        ],
    })
    data = response.json()
    assert data["answer"] != previous
    assert "重复内容" in data["answer"]
    assert data["total_tokens"] == HELP_USAGE["total_tokens"]
    assert data["fallback_reason"] == "invalid_help_response"


@pytest.mark.asyncio
async def test_failure_followup_does_not_repeat_full_guide(client, isolated_help):
    chat, _ = isolated_help
    attempt = model_attempt(status="unavailable", error_type="TimeoutError")
    chat.side_effect = AllModelsUnavailableError([attempt], dict(HELP_USAGE))
    response = await client.post("/api/v1/agent/chat", json={
        "question": "这两次回答咋一样", "history": [
            {"role": "user", "content": "我该如何使用你"},
            {"role": "assistant", "content": _product_help_answer("我该如何使用你")},
        ],
    })
    data = response.json()
    assert_product_help_metadata(data)
    assert "60 秒" in data["answer"]
    assert "重新发送这条追问" in data["answer"]


@pytest.mark.parametrize("question", [
    "我已经找到论文了，下一步怎么做？不要再重复软件介绍。",
    "我已经导入PDF了，接下来呢？",
    "我卡在选择来源这一步。具体一点，不要重复整段回答。",
    "已经下载论文了；然后呢？",
])
def test_compound_progress_remains_help_only_with_uninterrupted_help_history(question):
    assert _is_product_help(question, history_of("我该如何使用你"))
    assert not _is_product_help(question)
    assert not _is_product_help(question, history_of("我该如何使用你", "缓存延迟的结论是什么"))


@pytest.mark.parametrize("question", [
    "我已经找到论文了，下一步实验怎么设计？",
    "我已经导入PDF了，分析Transformer的注意力机制。",
    "我已经找到论文了，下一步比较这篇论文和RAG方法。",
    "不要再重复软件介绍，交通预测的平均误差是多少？",
    "我已经找到论文了，下一步怎么做？另外解释蛋白质折叠。",
])
def test_research_clause_cannot_be_hidden_inside_progress_followup(question):
    assert not _is_product_help(question, history_of("我该如何使用你"))


@pytest.mark.asyncio
async def test_live_compound_progress_calls_model_without_retrieval_or_false_clarification(client, isolated_help):
    chat, _ = isolated_help
    chat.side_effect = None
    chat.return_value = model_result("下一步在搜索页选中论文并分析；如果没有全文，请导入有权使用的 PDF。")
    question = "我已经找到论文了，下一步怎么做？不要再重复软件介绍。"
    response = await client.post("/api/v1/agent/chat", json={
        "question": question, "use_zotero": False,
        "history": [message.model_dump() for message in history_of("我该如何使用你？")],
    })
    assert response.status_code == 200
    data = response.json()
    assert_product_help_metadata(data)
    assert data["inference_mode"] == "model"
    assert data["answer"] == chat.return_value.content
    assert data["model_attempts"] and data["total_tokens"] == 100
    chat.assert_awaited_once()
    prompt = chat.await_args.kwargs["messages"]
    assert prompt[-1]["content"] == question
    assert "仅输出一个具体的进度澄清问句" not in prompt[0]["content"]
    assert "仅找到搜索结果不等于材料已进入智能体" in prompt[0]["content"]
    assert "导入有权使用的 PDF 或将分析结论保存到知识库" in prompt[0]["content"]
    assert len(data["answer"]) < 180
    assert "1. 准备材料" not in data["answer"]
