"""Offline integration guards for the private local-model provider and credentials."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app import config
from app.api.v1 import agent, model_config
from app.core.local_model import LOCAL_MODEL_NAME, LOCAL_MODEL_URL, validate_model_endpoint
from app.schemas.search import ModelCapabilityProbeRequest, ModelConfig, ModelTestRequest
from app.services.inference import model_router
from app.services.llm.gateway import LLMGateway


LOCAL_TOKEN = "offline-local-token-not-a-real-secret"
CLOUD_TOKEN = "offline-cloud-token-never-send-to-loopback"
LOCAL_PROFILE = {
    "provider": "local", "model": LOCAL_MODEL_NAME,
    "base_url": LOCAL_MODEL_URL, "api_key": LOCAL_TOKEN,
}
LOCAL_TASK = {
    "provider": "local", "model_name": LOCAL_MODEL_NAME,
    "base_url": LOCAL_MODEL_URL, "api_key": LOCAL_TOKEN,
}
MESSAGES = [{"role": "user", "content": "Keep this complete question intact."}]


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROFILES", deepcopy(config.MODEL_PROFILES))
    for name, value in {
        "DEBUG": False, "ALLOW_HTTP": False, "ALLOW_PRIVATE_IPS": False,
        "DEFAULT_LLM_PROVIDER": "openai", "OPENAI_API_KEY": CLOUD_TOKEN,
        "OPENAI_API_BASE": "https://cloud.example/v1", "OPENAI_DEFAULT_MODEL": "cloud-model",
    }.items():
        monkeypatch.setattr(config.settings, name, value)
    monkeypatch.setattr(model_config, "_read_saved_config", lambda: {})
    monkeypatch.setattr(model_config, "check_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(httpx, "AsyncClient", MagicMock(side_effect=AssertionError("Real network is forbidden")))


@pytest.mark.parametrize("url", [LOCAL_MODEL_URL, LOCAL_MODEL_URL + "/", "http://[::1]:8766/v1"])
def test_local_loopback_exception_does_not_weaken_custom_provider(url):
    assert validate_model_endpoint("local", url) == (True, None)
    assert validate_model_endpoint("custom", url)[0] is False


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8766/v1", "http://localhost:8766/v1", "http://127.0.0.2:8766/v1",
    "http://192.168.1.2:8766/v1", "http://external.example:8766/v1", "http://127.0.0.1/v1",
    "http://127.0.0.1:80/v1", "http://127.0.0.1:99999/v1", "http://127.0.0.1:8766/admin",
    "http://name:password@127.0.0.1:8766/v1", "http://127.0.0.1:8766/v1?target=cloud",
    "http://127.0.0.1:8766/v1#fragment",
])
def test_local_endpoint_policy_rejects_other_destinations(url):
    assert validate_model_endpoint("local", url)[0] is False


def mock_http(monkeypatch, *, status=200, finish="stop"):
    response = httpx.Response(status, request=httpx.Request("POST", LOCAL_MODEL_URL + "/chat/completions"), json={
        "choices": [{"message": {"content": "local answer"}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 41, "completion_tokens": 7, "total_tokens": 48},
        "error": {"message": "Input plus output exceeds the local context limit"},
    })
    post = AsyncMock(return_value=response)
    options = []
    class Client:
        def __init__(self, **kwargs):
            options.append(kwargs)
        async def __aenter__(self):
            return SimpleNamespace(post=post)
        async def __aexit__(self, *args):
            return None
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return post, options


@pytest.mark.parametrize("requested,expected", [(64, 64), (4096, 256)])
async def test_local_gateway_uses_only_local_token_and_counts_real_usage(monkeypatch, requested, expected):
    post, options = mock_http(monkeypatch)
    gateway = LLMGateway.from_profile(LOCAL_PROFILE)
    assert await gateway.chat(MESSAGES, max_tokens=requested, temperature=0.2) == "local answer"
    assert options == [{"trust_env": False, "follow_redirects": False, "timeout": 75.0}]
    sent = post.await_args.kwargs
    assert sent["url"] == LOCAL_MODEL_URL + "/chat/completions"
    assert sent["headers"] == {"Authorization": "Bearer " + LOCAL_TOKEN}
    assert sent["json"]["messages"] == MESSAGES
    assert sent["json"]["max_tokens"] == expected
    assert sent["json"]["stream"] is False
    assert CLOUD_TOKEN not in str(sent)
    assert gateway.usage == {
        "prompt_tokens": 41, "completion_tokens": 7, "total_tokens": 48, "requests": 1,
        "request_attempts": 1, "responses_received": 1, "usage_reports": 1,
    }


@pytest.mark.parametrize("explicit_profile", [False, True])
async def test_local_gateway_without_dedicated_token_never_uses_global_key(explicit_profile):
    gateway = LLMGateway.from_profile({**LOCAL_PROFILE, "api_key": None}) if explicit_profile else LLMGateway(provider="local")
    with pytest.raises(ValueError, match="凭证未配置"):
        await gateway.chat(MESSAGES)
    assert gateway.usage["request_attempts"] == 0


async def test_local_gateway_rejects_multimodal_before_sending():
    gateway = LLMGateway.from_profile(LOCAL_PROFILE)
    with pytest.raises(ValueError, match="仅接受文字"):
        await gateway.chat([{"role": "user", "content": [{"type": "image_url", "image_url": "private"}]}])
    assert gateway.usage["request_attempts"] == 0


async def test_context_rejection_keeps_original_prompt_and_has_no_automatic_retry(monkeypatch):
    post, _ = mock_http(monkeypatch, status=400)
    gateway = LLMGateway.from_profile(LOCAL_PROFILE)
    complete = [{"role": "user", "content": "LONG_" + "材料" * 3000 + "_TAIL"}]
    with pytest.raises(httpx.HTTPStatusError):
        await gateway.chat(complete, max_tokens=256)
    post.assert_awaited_once()
    assert post.await_args.kwargs["json"]["messages"] == complete
    assert gateway.usage["request_attempts"] == gateway.usage["responses_received"] == 1
    assert gateway.usage["usage_reports"] == 0


async def test_output_length_failure_preserves_billed_token_report(monkeypatch):
    mock_http(monkeypatch, finish="length")
    gateway = LLMGateway.from_profile(LOCAL_PROFILE)
    with pytest.raises(ValueError, match="长度上限"):
        await gateway.chat(MESSAGES)
    assert gateway.usage["total_tokens"] == 48
    assert gateway.usage["usage_reports"] == 1


@pytest.mark.parametrize("change", [
    {"provider": "local"}, {"tasks": {"vision": LOCAL_TASK}}, {"tasks": {"diagram": LOCAL_TASK}},
    {"fallback": {**LOCAL_TASK, "enabled": True}},
    {"embedding": {**LOCAL_TASK, "enabled": True}},
])
async def test_local_cannot_be_saved_as_global_or_non_assistant_task(change):
    request = ModelConfig(**{"provider": "openai", "model_name": "cloud-model", **change})
    with pytest.raises(HTTPException) as caught:
        await model_config.save_model_config(request)
    assert caught.value.status_code == 400
    assert not config.runtime_path("model_config.json").exists()


async def test_local_assistant_can_be_saved_without_replacing_cloud_default():
    request = ModelConfig(provider="openai", model_name="cloud-model", api_key=CLOUD_TOKEN,
                          tasks={"assistant": LOCAL_TASK})
    result = await model_config.save_model_config(request)
    assert result.success
    assert config.MODEL_PROFILES["assistant"] == LOCAL_PROFILE
    assert config.settings.DEFAULT_LLM_PROVIDER == "openai"
    assert config.settings.OPENAI_API_KEY == CLOUD_TOKEN
    assert config.MODEL_PROFILES["vision"]["provider"] == "openai"


@pytest.mark.parametrize("request_changes,expected", [
    ({}, LOCAL_TOKEN), ({"base_url": LOCAL_MODEL_URL + "/"}, LOCAL_TOKEN),
    ({"base_url": "http://127.0.0.1:8767/v1"}, None), ({"model_name": "other-model"}, None),
    ({"api_key": "explicit-local-test-token"}, "explicit-local-test-token"),
])
async def test_connection_reuses_saved_token_only_for_matching_local_assistant(monkeypatch, request_changes, expected):
    saved = {"provider": "openai", "api_key": CLOUD_TOKEN, "tasks": {"assistant": LOCAL_TASK}}
    monkeypatch.setattr(model_config, "_read_saved_config", lambda: saved)
    profiles = []
    def factory(profile):
        profiles.append(profile)
        return SimpleNamespace(test_connection=AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(LLMGateway, "from_profile", factory)
    request = ModelTestRequest(**{"provider": "local", "model_name": LOCAL_MODEL_NAME,
                                  "base_url": LOCAL_MODEL_URL, **request_changes})
    result = await model_config.test_model_connection(request, None)
    assert result.success
    assert len(profiles) == 1
    assert profiles[0]["api_key"] == expected
    assert CLOUD_TOKEN not in str(profiles)


@pytest.mark.parametrize("saved", [
    {"provider": "local", "api_key": CLOUD_TOKEN},
    {"provider": "openai", "fallback": {"provider": "local", "api_key": "stale-fallback-token"}},
])
async def test_connection_never_reuses_legacy_global_or_fallback_local_credentials(monkeypatch, saved):
    monkeypatch.setattr(model_config, "_read_saved_config", lambda: saved)
    monkeypatch.setattr(config.settings, "DEFAULT_LLM_PROVIDER", "local")
    profiles = []
    def factory(profile):
        profiles.append(profile)
        return SimpleNamespace(test_connection=AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(LLMGateway, "from_profile", factory)
    await model_config.test_model_connection(ModelTestRequest(provider="local", model_name=LOCAL_MODEL_NAME), None)
    assert profiles[0]["api_key"] is None
    assert profiles[0]["base_url"] == LOCAL_MODEL_URL


@pytest.mark.parametrize("request_changes,expected", [
    ({}, LOCAL_TOKEN), ({"base_url": "http://127.0.0.1:8767/v1"}, None),
    ({"model_name": "other-model"}, None),
])
def test_probe_reuses_local_token_only_for_matching_endpoint_and_model(monkeypatch, request_changes, expected):
    monkeypatch.setattr(model_config, "_read_saved_config", lambda: {
        "provider": "openai", "api_key": CLOUD_TOKEN, "tasks": {"assistant": LOCAL_TASK},
    })
    request = ModelCapabilityProbeRequest(**{"provider": "local", "model_name": LOCAL_MODEL_NAME,
                                            "base_url": LOCAL_MODEL_URL, "task": "assistant", **request_changes})
    profile = model_config._resolve_probe_profile(request)
    assert profile["api_key"] == expected
    assert CLOUD_TOKEN not in str(profile)


def test_probe_legacy_global_local_never_inherits_cloud_settings_key(monkeypatch):
    monkeypatch.setattr(model_config, "_read_saved_config", lambda: {"provider": "local", "api_key": CLOUD_TOKEN})
    monkeypatch.setattr(config.settings, "DEFAULT_LLM_PROVIDER", "local")
    profile = model_config._resolve_probe_profile(ModelCapabilityProbeRequest(
        provider="local", model_name=LOCAL_MODEL_NAME, base_url=LOCAL_MODEL_URL, task="assistant",
    ))
    assert profile["api_key"] is None
    assert profile["base_url"] == LOCAL_MODEL_URL


async def test_failed_local_primary_never_calls_enabled_cloud_fallback(monkeypatch):
    monkeypatch.setattr(model_router, "get_fallback_model_config", lambda: {
        "enabled": True, "provider": "openai", "model": "cloud-model", "api_key": CLOUD_TOKEN,
    })
    profiles = []
    class Gateway:
        @classmethod
        def from_profile(cls, profile):
            profiles.append(profile)
            return SimpleNamespace(chat=AsyncMock(side_effect=RuntimeError("offline local failure")), usage={})
    with pytest.raises(model_router.AllModelsUnavailableError) as caught:
        await model_router.chat_with_fallback(
            task="assistant", messages=MESSAGES, temperature=0.2, max_tokens=256,
            profile=LOCAL_PROFILE, gateway_factory=Gateway, allow_fallback=True,
        )
    assert profiles == [LOCAL_PROFILE]
    assert len(caught.value.attempts) == 1
    assert caught.value.attempts[0].provider == "local"


async def test_product_help_with_local_token_is_model_backed_and_uses_small_context(monkeypatch):
    monkeypatch.setattr(agent, "get_model_for_task", lambda _: LOCAL_PROFILE)
    usage = {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42, "requests": 1}
    attempt = model_router.ModelAttempt(role="primary", provider="local", model=LOCAL_MODEL_NAME,
                                        status="completed", **usage)
    chat = AsyncMock(return_value=model_router.RoutedChatResult(
        content="先在搜索页检索论文，再选择材料来源提出问题。", profile=LOCAL_PROFILE,
        usage=usage, attempts=(attempt,), fallback_used=False,
    ))
    monkeypatch.setattr(agent, "chat_with_fallback", chat)
    history = [agent.AgentMessage(role="user", content="OLD_SENTINEL"),
               agent.AgentMessage(role="assistant", content="最近回复" * 150),
               agent.AgentMessage(role="user", content="最近问题" * 150)]
    question = "我该如何使用你？请不要遗漏这句话的末尾_SENTINEL"
    result = await agent._answer_product_help(agent.AgentChatRequest(question=question, history=history))
    assert result.inference_mode == "model" and result.provider == "local"
    assert result.total_tokens == 42 and not result.fallback_used
    options = chat.await_args.kwargs
    assert options["profile"] == LOCAL_PROFILE
    assert options["allow_fallback"] is False
    assert options["messages"][-1] == {"role": "user", "content": question}
    assert options["messages"][1:-1] == [
        {"role": message.role, "content": message.content[:200]} for message in history[-2:]
    ]
    prompt = "\n".join(message["content"] for message in options["messages"])
    assert len(prompt) < 1600
    assert "OLD_SENTINEL" not in prompt
    assert LOCAL_TOKEN not in prompt + str(result.model_dump())
    chat.assert_awaited_once()
