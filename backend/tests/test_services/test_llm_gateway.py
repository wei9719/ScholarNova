"""
LLM 网关测试
"""

import asyncio
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm.gateway import LLMGateway


@pytest.fixture(autouse=True)
def synthetic_trust_context(monkeypatch):
    # Transport behavior is mocked here; trust-store behavior has separate tests.
    monkeypatch.setattr(
        "app.services.llm.gateway._load_ssl_context",
        AsyncMock(return_value=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)),
    )


class TestLLMGateway:
    """LLMGateway 测试套件"""

    def test_default_provider(self):
        """默认 provider 应从 settings 获取"""
        gateway = LLMGateway()
        assert gateway.provider is not None

    def test_custom_provider(self):
        """应支持自定义 provider"""
        gateway = LLMGateway(provider="ollama")
        assert gateway.provider == "ollama"

    def test_client_initially_none(self):
        """初始化时 _client 应为 None"""
        gateway = LLMGateway(provider="openai")
        assert gateway._client is None

    async def test_chat_unsupported_provider(self):
        """不支持的 provider 应抛出 ValueError"""
        gateway = LLMGateway(provider="unsupported")
        with pytest.raises(ValueError, match="Unsupported provider"):
            await gateway.chat(messages=[{"role": "user", "content": "hello"}])

    async def test_qwen_profile_uses_openai_compatible_route(self):
        """千问配置应使用独立凭据走 OpenAI 兼容协议。"""
        gateway = LLMGateway.from_profile(
            {
                "provider": "qwen",
                "model": "qwen-plus",
                "api_key": "qwen-key",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            }
        )
        gateway._chat_openai = AsyncMock(return_value="Qwen ready")

        result = await gateway.chat(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=20,
        )

        assert result == "Qwen ready"
        assert gateway.provider == "qwen"
        assert gateway._api_key == "qwen-key"
        assert gateway._model_name == "qwen-plus"
        gateway._chat_openai.assert_awaited_once()

    async def test_siliconflow_qwen_uses_openai_compatible_route(self):
        """硅基流动 Qwen 应使用其独立 Key 和 OpenAI 兼容地址。"""
        gateway = LLMGateway.from_profile(
            {
                "provider": "siliconflow",
                "model": "Qwen/Qwen3-8B",
                "api_key": "siliconflow-key",
                "base_url": "https://api.siliconflow.cn/v1",
            }
        )
        gateway._chat_openai = AsyncMock(return_value="SiliconFlow Qwen ready")

        result = await gateway.chat(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=20,
        )

        assert result == "SiliconFlow Qwen ready"
        assert gateway.provider == "siliconflow"
        assert gateway._api_key == "siliconflow-key"
        assert gateway._base_url == "https://api.siliconflow.cn/v1"
        gateway._chat_openai.assert_awaited_once()

    async def test_explicit_qwen_profile_never_inherits_another_provider_key(self):
        """隔离配置缺少 Key 时应本地失败，而不是读取全局 OpenAI Key。"""
        gateway = LLMGateway.from_profile(
            {
                "provider": "qwen",
                "model": "qwen-plus",
                "api_key": None,
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            }
        )

        with pytest.raises(ValueError, match="qwen"):
            await gateway._chat_openai_once(
                messages=[{"role": "user", "content": "hello"}],
                model="qwen-plus",
                temperature=0.2,
                max_tokens=20,
            )

    async def test_chat_openai_success(self):
        """OpenAI 调用成功应返回响应文本"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello, world!"
        mock_response.usage = MagicMock(
            prompt_tokens=12,
            completion_tokens=4,
            total_tokens=16,
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gateway = LLMGateway(provider="openai")

        with patch("app.services.llm.gateway.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            mock_settings.OPENAI_API_BASE = "https://api.openai.com/v1"
            mock_settings.OPENAI_DEFAULT_MODEL = "gpt-4o"

            with patch("openai.AsyncOpenAI", return_value=mock_client):
                result = await gateway.chat(
                    messages=[{"role": "user", "content": "Say hello"}],
                    max_tokens=10,
                )

        assert result == "Hello, world!"
        mock_client.close.assert_awaited_once()
        assert gateway._client is None
        assert gateway.last_usage == {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
            "requests": 1,
        }
        assert gateway.usage["total_tokens"] == 16
        assert gateway.usage["request_attempts"] == 1
        assert gateway.usage["responses_received"] == 1
        assert gateway.usage["usage_reports"] == 1

    async def test_usage_accumulates_and_resets(self):
        """连续模型调用应累计真实 usage，并支持按评测样本清零。"""
        gateway = LLMGateway(provider="openai")
        gateway._record_usage(prompt_tokens=10, completion_tokens=2)
        gateway._record_usage(prompt_tokens=20, completion_tokens=3)

        assert gateway.usage == {
            "prompt_tokens": 30,
            "completion_tokens": 5,
            "total_tokens": 35,
            "requests": 2,
            "request_attempts": 0,
            "responses_received": 0,
            "usage_reports": 0,
        }

        gateway.reset_usage()
        assert gateway.usage == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
            "request_attempts": 0,
            "responses_received": 0,
            "usage_reports": 0,
        }

    async def test_chat_openai_rebuilds_client_after_connection_error(self):
        """A transient connection error should rebuild the client and retry."""
        import httpx
        import openai

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Recovered"

        failed_client = AsyncMock()
        failed_client.chat.completions.create = AsyncMock(
            side_effect=openai.APIConnectionError(
                request=httpx.Request("POST", "https://example.com/v1/chat/completions")
            )
        )
        healthy_client = AsyncMock()
        healthy_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gateway = LLMGateway(provider="openai")

        with patch("app.services.llm.gateway.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            mock_settings.OPENAI_API_BASE = "https://example.com/v1"
            mock_settings.OPENAI_DEFAULT_MODEL = "test-model"
            mock_settings.LLM_MAX_RETRIES = 1

            with patch("openai.AsyncOpenAI", side_effect=[failed_client, healthy_client]):
                with patch("app.services.llm.gateway.asyncio.sleep", new=AsyncMock()):
                    result = await gateway.chat(
                        messages=[{"role": "user", "content": "Say hello"}],
                        max_tokens=10,
                    )

        assert result == "Recovered"
        failed_client.close.assert_awaited_once()
        healthy_client.close.assert_awaited_once()
        assert failed_client.chat.completions.create.await_count == 1
        assert healthy_client.chat.completions.create.await_count == 1
        assert gateway.usage["request_attempts"] == 2
        assert gateway.usage["responses_received"] == 1

    async def test_openai_probe_retry_override_is_not_sent_to_provider(self):
        """Internal retry controls must not leak into compatible API payloads."""
        gateway = LLMGateway(provider="zhipu")
        gateway._chat_openai_once = AsyncMock(return_value="OK")

        with patch("app.services.llm.gateway.settings") as mock_settings:
            mock_settings.LLM_MAX_RETRIES = 2
            mock_settings.LLM_TIMEOUT = 60
            mock_settings.OPENAI_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
            mock_settings.OPENAI_DEFAULT_MODEL = "glm-5.2"
            result = await gateway.chat(
                messages=[{"role": "user", "content": "hello"}],
                _max_retries=0,
                extra_body={"thinking": {"type": "disabled"}},
            )

        assert result == "OK"
        forwarded = gateway._chat_openai_once.await_args.kwargs
        assert "_max_retries" not in forwarded
        assert forwarded["extra_body"] == {"thinking": {"type": "disabled"}}

    async def test_chat_ollama_success(self):
        """Ollama 调用成功应返回响应文本"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "Hello from Ollama"},
            "prompt_eval_count": 8,
            "eval_count": 3,
        }
        mock_response.raise_for_status = MagicMock()

        gateway = LLMGateway(provider="ollama")

        with patch("app.services.llm.gateway.settings") as mock_settings:
            mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
            mock_settings.OLLAMA_DEFAULT_MODEL = "qwen2.5:14b"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_http_client = AsyncMock()
                mock_http_client.post = AsyncMock(return_value=mock_response)
                mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
                mock_http_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_http_client

                result = await gateway.chat(
                    messages=[{"role": "user", "content": "Say hello"}],
                    max_tokens=10,
                )

        assert result == "Hello from Ollama"
        assert gateway.usage["total_tokens"] == 11
        assert gateway.usage["request_attempts"] == 1
        assert gateway.usage["responses_received"] == 1
        assert gateway.usage["usage_reports"] == 1

    async def test_test_connection_success(self):
        """test_connection 成功应返回 success=True"""
        gateway = LLMGateway(provider="openai")
        gateway.chat = AsyncMock(return_value="hello")

        result = await gateway.test_connection()
        assert result["success"] is True
        assert "model" in result

    async def test_test_connection_failure(self):
        """test_connection 失败应返回 success=False"""
        gateway = LLMGateway(provider="openai")
        gateway.chat = AsyncMock(side_effect=Exception("Connection failed"))

        result = await gateway.test_connection()
        assert result["success"] is False
        assert "error" in result
        assert "Connection failed" in result["error"]

    async def test_zhipu_connection_probe_disables_thinking_and_retries(self):
        """GLM probes should reserve tokens for a visible answer and fail fast."""
        gateway = LLMGateway(provider="zhipu")
        gateway.chat = AsyncMock(return_value="hello")

        result = await gateway.test_connection()

        assert result["success"] is True
        assert gateway.chat.await_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert gateway.chat.await_args.kwargs["_max_retries"] == 0

    async def test_siliconflow_connection_probe_disables_thinking_and_retries(self):
        """SiliconFlow Qwen probes should return visible text without SDK retries."""
        gateway = LLMGateway(provider="siliconflow")
        gateway.chat = AsyncMock(return_value="hello")

        result = await gateway.test_connection()

        assert result["success"] is True
        assert gateway.chat.await_args.kwargs["extra_body"] == {"enable_thinking": False}
        assert gateway.chat.await_args.kwargs["_max_retries"] == 0

    async def test_chat_anthropic_success(self):
        """Anthropic 调用成功应返回响应文本"""
        mock_content = MagicMock()
        mock_content.text = "Hello from Claude"

        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        gateway = LLMGateway(provider="anthropic")

        with patch("app.services.llm.gateway.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "test-key"
            mock_settings.ANTHROPIC_DEFAULT_MODEL = "claude-3-5-sonnet-20241022"

            with patch("anthropic.AsyncAnthropic", return_value=mock_client):
                result = await gateway.chat(
                    messages=[
                        {"role": "system", "content": "You are helpful"},
                        {"role": "user", "content": "Say hello"},
                    ],
                    max_tokens=10,
                )

        assert result == "Hello from Claude"
        mock_client.close.assert_awaited_once()
        assert gateway._client is None

    async def test_chat_anthropic_extracts_system_message(self):
        """Anthropic 调用应提取消息中的 system 消息"""
        mock_content = MagicMock()
        mock_content.text = "OK"
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        gateway = LLMGateway(provider="anthropic")

        with patch("app.services.llm.gateway.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "test-key"
            mock_settings.ANTHROPIC_DEFAULT_MODEL = "claude-3-5-sonnet-20241022"

            with patch("anthropic.AsyncAnthropic", return_value=mock_client):
                await gateway.chat(
                    messages=[
                        {"role": "system", "content": "Be concise"},
                        {"role": "user", "content": "Hello"},
                    ],
                )

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["system"] == "Be concise"

    @pytest.mark.parametrize("status_code", [200, 529])
    async def test_anthropic_internal_retry_budget_is_not_sent_to_messages(self, status_code):
        """Use the real SDK on a fake transport to exercise its strict signature."""
        from importlib import import_module
        import json

        import anthropic

        # New SDKs can use httpx2 even when this application also has httpx.
        # Match the SDK's actual client base, not whichever package is installed.
        http_client_base = next(
            base for base in anthropic.DefaultAsyncHttpxClient.__mro__
            if base.__name__ == "AsyncClient"
            and base.__module__.split(".", 1)[0] in {"httpx", "httpx2"}
        )
        sdk_http = import_module(http_client_base.__module__.split(".", 1)[0])

        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            if status_code != 200:
                return sdk_http.Response(
                    status_code,
                    json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
                )
            return sdk_http.Response(200, json={
                "id": "test-message", "type": "message", "role": "assistant",
                "model": "test-model", "content": [{"type": "text", "text": "Grounded answer [S1]"}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 4},
            })

        async with anthropic.DefaultAsyncHttpxClient(
            transport=sdk_http.MockTransport(respond),
        ) as http_client:
            async with anthropic.AsyncAnthropic(
                api_key="test-key", base_url="https://anthropic.test", http_client=http_client,
                max_retries=2,
            ) as sdk_client:
                gateway = LLMGateway(provider="anthropic")
                gateway._client = sdk_client
                gateway._model_name = "test-model"
                request = gateway.chat(
                    [{"role": "user", "content": "Summarize [S1]"}],
                    max_tokens=100, _max_retries=0,
                )
                if status_code == 200:
                    assert await request == "Grounded answer [S1]"
                    assert gateway.usage["total_tokens"] == 16
                else:
                    with pytest.raises(anthropic.APIStatusError) as error:
                        await request
                    assert error.value.status_code == 529
                # A request-local cap must not mutate later callers' defaults.
                assert sdk_client.max_retries == 2

        assert len(requests) == 1
        assert "_max_retries" not in requests[0]
        assert requests[0]["model"] == "test-model"
        assert gateway.usage["request_attempts"] == 1
        assert gateway.usage["responses_received"] == 1
        assert gateway.usage["usage_reports"] == int(status_code == 200)

    @pytest.mark.parametrize("reported_usage", [None, {"prompt_tokens": 0, "completion_tokens": 0}])
    async def test_openai_response_without_usage_is_not_reported_as_known_zero(self, reported_usage):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            usage=reported_usage,
        )
        client = AsyncMock()
        client.chat.completions.create.return_value = response
        gateway = LLMGateway.from_profile({
            "provider": "siliconflow", "model": "offline-qwen", "api_key": "dummy",
            "base_url": "http://127.0.0.1:9/v1",
        })
        with patch("openai.AsyncOpenAI", return_value=client):
            assert await gateway.chat([{"role": "user", "content": "hello"}], _max_retries=0) == "OK"
        assert gateway.usage == {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "requests": 1, "request_attempts": 1, "responses_received": 1,
            "usage_reports": int(reported_usage is not None),
        }
        gateway.reset_usage()
        assert all(value == 0 for value in gateway.usage.values())

    @pytest.mark.parametrize("provider", ["siliconflow", "anthropic"])
    async def test_cancelled_sdk_call_closes_client_without_retry_or_fake_usage(self, provider):
        started = asyncio.Event()

        async def wait_for_cancel(**kwargs):
            started.set()
            await asyncio.Event().wait()

        client = AsyncMock()
        client.with_options = MagicMock(return_value=client)
        client.chat.completions.create.side_effect = wait_for_cancel
        client.messages.create.side_effect = wait_for_cancel
        gateway = LLMGateway.from_profile({
            "provider": provider, "model": "offline-model", "api_key": "dummy",
            "base_url": "http://127.0.0.1:9/v1",
        })
        factory = "anthropic.AsyncAnthropic" if provider == "anthropic" else "openai.AsyncOpenAI"
        with patch(factory, return_value=client):
            request = asyncio.create_task(gateway.chat(
                [{"role": "user", "content": "hello"}], _max_retries=0,
            ))
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
            finally:
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
        client.close.assert_awaited_once()
        assert gateway._client is None
        assert gateway.usage == {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "requests": 0, "request_attempts": 1, "responses_received": 0, "usage_reports": 0,
        }
