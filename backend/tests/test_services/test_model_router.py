"""Tests for explicit primary/fallback model routing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.inference.model_router import (
    AllModelsUnavailableError,
    RoutedLLMGateway,
    chat_with_fallback,
)


class RoutedGateway:
    profiles: list[dict] = []
    fail_providers: set[str] = set()

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self._usage = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "requests": 1,
        }
        self.profiles.append(profile)

    @classmethod
    def from_profile(cls, profile: dict):
        return cls(profile)

    @property
    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    async def chat(self, messages, **kwargs) -> str:
        del messages, kwargs
        if self.profile["provider"] in self.fail_providers:
            raise RuntimeError("offline")
        return f"answer from {self.profile['provider']}"


@pytest.mark.asyncio
async def test_repair_timeout_stops_without_calling_fallback(monkeypatch):
    class SlowGateway(RoutedGateway):
        async def chat(self, messages, **kwargs):
            assert kwargs["_max_retries"] == 0
            await asyncio.sleep(10)
    SlowGateway.profiles = []
    monkeypatch.setattr(
        "app.services.inference.model_router.get_fallback_model_config",
        lambda: {"enabled": True, "provider": "qwen", "model": "qwen-plus"},
    )
    with pytest.raises(AllModelsUnavailableError) as caught:
        await chat_with_fallback(
            task="assistant", messages=[{"role": "user", "content": "repair"}],
            temperature=0, max_tokens=900, gateway_factory=SlowGateway,
            profile={"provider": "zhipu", "model": "test"},
            allow_fallback=False, timeout_seconds=0.01,
        )
    assert len(caught.value.attempts) == 1
    assert caught.value.attempts[0].error_type == "TimeoutError"
    assert len(SlowGateway.profiles) == 1


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback(monkeypatch) -> None:
    RoutedGateway.profiles = []
    RoutedGateway.fail_providers = set()
    monkeypatch.setattr(
        "app.services.inference.model_router.get_model_for_task",
        lambda task: {
            "provider": "zhipu", "model": "glm-5.2", "api_key": "primary",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
        },
    )
    monkeypatch.setattr(
        "app.services.inference.model_router.get_fallback_model_config",
        lambda: {
            "enabled": True, "provider": "qwen", "model": "qwen-plus",
            "api_key": "fallback", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
    )

    result = await chat_with_fallback(
        task="assistant",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.2,
        max_tokens=100,
        gateway_factory=RoutedGateway,
    )

    assert result.content == "answer from zhipu"
    assert result.fallback_used is False
    assert [profile["provider"] for profile in RoutedGateway.profiles] == ["zhipu"]
    assert result.usage["total_tokens"] == 15


@pytest.mark.asyncio
async def test_primary_failure_routes_to_qwen_and_sums_usage(monkeypatch) -> None:
    RoutedGateway.profiles = []
    RoutedGateway.fail_providers = {"zhipu"}
    monkeypatch.setattr(
        "app.services.inference.model_router.get_model_for_task",
        lambda task: {
            "provider": "zhipu", "model": "glm-5.2", "api_key": "primary",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
        },
    )
    monkeypatch.setattr(
        "app.services.inference.model_router.get_fallback_model_config",
        lambda: {
            "enabled": True, "provider": "qwen", "model": "qwen-plus",
            "api_key": "fallback", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
    )

    result = await chat_with_fallback(
        task="assistant",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.2,
        max_tokens=100,
        gateway_factory=RoutedGateway,
    )

    assert result.content == "answer from qwen"
    assert result.fallback_used is True
    assert [attempt.status for attempt in result.attempts] == ["unavailable", "completed"]
    assert result.usage["total_tokens"] == 30


@pytest.mark.asyncio
async def test_all_routes_failure_exposes_attempts_without_raw_errors(monkeypatch) -> None:
    RoutedGateway.profiles = []
    RoutedGateway.fail_providers = {"zhipu", "qwen"}
    monkeypatch.setattr(
        "app.services.inference.model_router.get_model_for_task",
        lambda task: {
            "provider": "zhipu", "model": "glm-5.2", "api_key": "primary",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
        },
    )
    monkeypatch.setattr(
        "app.services.inference.model_router.get_fallback_model_config",
        lambda: {
            "enabled": True, "provider": "qwen", "model": "qwen-plus",
            "api_key": "fallback", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
    )

    with pytest.raises(AllModelsUnavailableError) as caught:
        await chat_with_fallback(
            task="assistant",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.2,
            max_tokens=100,
            gateway_factory=RoutedGateway,
        )

    assert [attempt.provider for attempt in caught.value.attempts] == ["zhipu", "qwen"]
    assert all(attempt.error_type == "RuntimeError" for attempt in caught.value.attempts)
    assert caught.value.usage["total_tokens"] == 30


@pytest.mark.asyncio
async def test_fallback_is_not_used_for_vision_task(monkeypatch) -> None:
    RoutedGateway.profiles = []
    RoutedGateway.fail_providers = {"mimo"}
    monkeypatch.setattr(
        "app.services.inference.model_router.get_model_for_task",
        lambda task: {"provider": "mimo", "model": "mimo-v2.5", "api_key": "primary", "base_url": "https://example.com/v1"},
    )
    monkeypatch.setattr(
        "app.services.inference.model_router.get_fallback_model_config",
        lambda: {"enabled": True, "provider": "qwen", "model": "qwen-plus"},
    )

    with pytest.raises(AllModelsUnavailableError) as caught:
        await chat_with_fallback(
            task="vision",
            messages=[{"role": "user", "content": "image"}],
            temperature=0.2,
            max_tokens=100,
            gateway_factory=RoutedGateway,
        )

    assert len(caught.value.attempts) == 1


@pytest.mark.asyncio
async def test_gateway_adapter_preserves_chat_contract_and_usage(monkeypatch) -> None:
    RoutedGateway.profiles = []
    RoutedGateway.fail_providers = set()
    monkeypatch.setattr(
        "app.services.inference.model_router.get_model_for_task",
        lambda task: {
            "provider": "qwen", "model": "qwen-plus", "api_key": "key",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
    )
    monkeypatch.setattr(
        "app.services.inference.model_router.get_fallback_model_config",
        lambda: {"enabled": False},
    )
    gateway = RoutedLLMGateway("query_planning", gateway_factory=RoutedGateway)

    content = await gateway.chat([{"role": "user", "content": "plan"}])

    assert content == "answer from qwen"
    assert gateway.last_usage["total_tokens"] == 15
    assert gateway.usage["requests"] == 1


@pytest.mark.asyncio
async def test_query_planning_timeout_leaves_time_for_fallback(monkeypatch) -> None:
    class TimeoutGateway(RoutedGateway):
        async def chat(self, messages, **kwargs) -> str:
            del messages, kwargs
            if self.profile["provider"] == "zhipu":
                await asyncio.sleep(1)
            return f"answer from {self.profile['provider']}"

    TimeoutGateway.profiles = []
    monkeypatch.setattr(
        "app.services.inference.model_router.get_model_for_task",
        lambda task: {
            "provider": "zhipu", "model": "glm-5.2", "api_key": "primary",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
        },
    )
    monkeypatch.setattr(
        "app.services.inference.model_router.get_fallback_model_config",
        lambda: {
            "enabled": True, "provider": "qwen", "model": "qwen-plus",
            "api_key": "fallback", "base_url": "https://example.com/v1",
        },
    )
    monkeypatch.setattr(
        "app.services.inference.model_router._route_timeout",
        lambda task: 0.01,
    )

    result = await chat_with_fallback(
        task="query_planning",
        messages=[{"role": "user", "content": "plan"}],
        temperature=0.2,
        max_tokens=100,
        gateway_factory=TimeoutGateway,
    )

    assert result.content == "answer from qwen"
    assert result.fallback_used is True
    assert [attempt.status for attempt in result.attempts] == [
        "unavailable",
        "completed",
    ]


@pytest.mark.asyncio
async def test_gateway_adapter_disables_thinking_and_preserves_failed_usage(monkeypatch):
    class PlanningGateway(RoutedGateway):
        async def chat(self, messages, **kwargs):
            assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
            return await super().chat(messages, **kwargs)

    PlanningGateway.profiles = []
    PlanningGateway.fail_providers = set()
    monkeypatch.setattr("app.services.inference.model_router.get_model_for_task", lambda task: {
        "provider": "zhipu", "model": "glm-5.2", "api_key": "test", "base_url": "https://example.test",
    })
    monkeypatch.setattr("app.services.inference.model_router.get_fallback_model_config", lambda: {"enabled": False})
    gateway = RoutedLLMGateway("analysis", gateway_factory=PlanningGateway)
    await gateway.chat([{"role": "user", "content": "plan"}])
    assert gateway.last_result is not None
    PlanningGateway.fail_providers = {"zhipu"}

    with pytest.raises(AllModelsUnavailableError):
        await gateway.chat([{"role": "user", "content": "plan again"}])

    assert gateway.last_result is None
    assert gateway.last_usage["total_tokens"] == 15
    assert gateway.usage["total_tokens"] == 30
    assert gateway.usage["requests"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "http-error", "no-usage", "reported-usage"])
async def test_real_gateway_router_reports_transport_and_usage_independently(monkeypatch, outcome):
    import httpx
    import openai
    import ssl

    from app.services.llm.gateway import LLMGateway

    client = AsyncMock()
    if outcome == "timeout":
        async def wait_for_cancel(**kwargs):
            await asyncio.Event().wait()
        client.chat.completions.create.side_effect = wait_for_cancel
    elif outcome == "http-error":
        client.chat.completions.create.side_effect = openai.AuthenticationError(
            "invalid dummy-private-key",
            response=httpx.Response(401, request=httpx.Request("POST", "https://offline.test/chat")),
            body=None,
        )
    else:
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Helpful answer"))],
            usage={"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
            if outcome == "reported-usage" else None,
        )
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **kwargs: client)
    monkeypatch.setattr(
        "app.services.llm.gateway._load_ssl_context",
        AsyncMock(return_value=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)),
    )
    monkeypatch.setattr("app.services.inference.model_router.get_fallback_model_config", lambda: {
        "enabled": True, "provider": "qwen", "model": "must-not-use-fallback",
    })
    request = chat_with_fallback(
        task="assistant", messages=[{"role": "user", "content": "hello"}],
        temperature=0.2, max_tokens=240, gateway_factory=LLMGateway,
        profile={"provider": "siliconflow", "model": "offline-qwen", "api_key": "dummy",
                 "base_url": "http://127.0.0.1:9/v1"},
        allow_fallback=False, timeout_seconds=0.02 if outcome == "timeout" else 1,
    )
    if outcome in {"timeout", "http-error"}:
        with pytest.raises(AllModelsUnavailableError) as caught:
            await request
        result = caught.value
        assert result.attempts[0].status == "unavailable"
        assert result.attempts[0].error_type == (
            "TimeoutError" if outcome == "timeout" else "AuthenticationError"
        )
        client.close.assert_awaited_once()
    else:
        result = await request
        assert result.content == "Helpful answer"
        assert result.attempts[0].status == "completed"
        assert result.fallback_used is False
    assert len(result.attempts) == 1
    attempt = result.attempts[0].to_dict()
    assert attempt["request_attempts"] == result.usage["request_attempts"] == 1
    assert attempt["responses_received"] == result.usage["responses_received"] == int(outcome != "timeout")
    assert attempt["usage_reports"] == result.usage["usage_reports"] == int(outcome == "reported-usage")
    assert attempt["total_tokens"] == result.usage["total_tokens"] == (16 if outcome == "reported-usage" else 0)
    assert result.usage["requests"] == int(outcome in {"no-usage", "reported-usage"})
    assert "dummy-private-key" not in str(attempt)
    client.chat.completions.create.assert_awaited_once()
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["extra_body"] == {"enable_thinking": False}
    assert "_max_retries" not in kwargs


@pytest.mark.asyncio
async def test_query_planner_adapter_keeps_useful_budget_without_second_model(monkeypatch):
    from app.services.search.query_planner import QueryPlanner
    from app.schemas.query import DataSource

    budgets = []

    async def virtual_wait_for(request, timeout):
        # A simulated 15-second provider fits the new budget, not the old 5.5s.
        budgets.append(timeout)
        assert timeout >= 15.0
        return await request

    class PlanningGateway(RoutedGateway):
        async def chat(self, messages, **kwargs):
            assert kwargs["_max_retries"] == 0
            return '{"sub_queries":[{"source":"openalex","query":"RAG evaluation"}]}'

    PlanningGateway.profiles = []
    monkeypatch.setattr("app.services.inference.model_router.get_model_for_task", lambda task: {
        "provider": "zhipu", "model": "offline-primary",
    })
    monkeypatch.setattr("app.services.inference.model_router.get_fallback_model_config", lambda: {
        "enabled": True, "provider": "qwen", "model": "must-not-be-called",
    })
    monkeypatch.setattr("app.services.inference.model_router.asyncio.wait_for", virtual_wait_for)
    gateway = RoutedLLMGateway("query_planning", gateway_factory=PlanningGateway)
    planner = QueryPlanner(gateway)
    result = await planner.plan("RAG", [DataSource.OPENALEX], planning_mode="ai")
    assert budgets == [21.0, 20.0]
    assert planner.planning_mode == "ai" and result.sub_queries[0].query == "RAG evaluation"
    assert len(PlanningGateway.profiles) == 1
    assert gateway.usage["total_tokens"] == 15 and gateway.usage["requests"] == 1


@pytest.mark.asyncio
async def test_query_planning_timeout_retains_attempt_without_inventing_zero_cost(monkeypatch):
    from app.services.search.query_planner import QueryPlanner
    from app.schemas.query import DataSource

    class SlowPlanningGateway(RoutedGateway):
        async def chat(self, messages, **kwargs):
            assert kwargs["_max_retries"] == 0
            self._usage = {
                "request_attempts": 1, "requests": 0, "responses_received": 0,
                "usage_reports": 0, "total_tokens": 0,
            }
            await asyncio.sleep(60)

    SlowPlanningGateway.profiles = []
    monkeypatch.setattr("app.services.inference.model_router.get_model_for_task", lambda task: {
        "provider": "zhipu", "model": "offline-primary",
    })
    monkeypatch.setattr("app.services.inference.model_router.get_fallback_model_config", lambda: {
        "enabled": True, "provider": "qwen", "model": "must-not-be-called",
    })
    gateway = RoutedLLMGateway("query_planning", gateway_factory=SlowPlanningGateway)
    planner = QueryPlanner(gateway)
    monkeypatch.setattr(planner, "AI_TIMEOUT_SECONDS", 0.01)
    result = await planner.plan("RAG", [DataSource.OPENALEX], planning_mode="ai")
    assert planner.planning_mode == "fallback" and "可能产生费用" in result.strategy
    assert len(SlowPlanningGateway.profiles) == 1
    assert gateway.usage["request_attempts"] == 1
    assert gateway.usage["requests"] == gateway.usage["usage_reports"] == 0
    assert gateway.last_result is None
