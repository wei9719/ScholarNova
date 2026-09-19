"""
LLM 网关

统一封装多家 LLM 提供商的调用接口，支持:
- OpenAI（含兼容 API）
- Anthropic
- Ollama（本地模型）
"""

import asyncio
import json
import logging
from numbers import Number
from typing import Any, List, Optional
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger(__name__)


class EmptyLLMResponseError(RuntimeError):
    """The provider returned a successful response without usable text."""


class LLMGateway:
    """
    LLM 统一调用网关

    支持按任务类型自动选择模型（多模型配置）。
    """

    def __init__(self, provider: Optional[str] = None, task: Optional[str] = None):
        """
        初始化 LLM 网关

        Args:
            provider: LLM 提供商名称
            task: 任务类型（analysis/query_planning/translation/vision/recommendation）
        """
        self._explicit_profile = False
        if task:
            from app.config import get_model_for_task

            profile = get_model_for_task(task)
            self.provider = profile["provider"]
            self._api_key = profile["api_key"]
            self._base_url = profile["base_url"]
            self._model_name = profile["model"]
        else:
            self.provider = provider or settings.DEFAULT_LLM_PROVIDER
            self._api_key = None
            self._base_url = None
            self._model_name = None
        self._client = None
        self._usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
        }
        self.last_usage = dict(self._usage)
        self._usage.update(request_attempts=0, responses_received=0, usage_reports=0)

        # SenseNova 默认配置
        if self.provider == "sensenova" and not self._api_key:
            self._api_key = settings.SENSENOVA_API_KEY
            self._base_url = settings.SENSENOVA_API_BASE
            self._model_name = self._model_name or settings.SENSENOVA_DEFAULT_MODEL

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "LLMGateway":
        """Build a gateway from one isolated profile without credential inheritance."""
        gateway = cls(provider=str(profile.get("provider") or "openai"))
        gateway._explicit_profile = True
        gateway._api_key = profile.get("api_key")
        gateway._base_url = (
            str(profile["base_url"]).rstrip("/") if profile.get("base_url") else None
        )
        gateway._model_name = profile.get("model") or profile.get("model_name")
        gateway._client = None
        return gateway

    def configure(self, api_key: str = None, base_url: str = None, model_name: str = None):
        """运行时覆盖配置"""
        config_changed = False
        if api_key:
            config_changed = config_changed or api_key != self._api_key
            self._api_key = api_key
        if base_url:
            normalized_base_url = base_url.rstrip("/")
            config_changed = config_changed or normalized_base_url != self._base_url
            self._base_url = normalized_base_url
        if model_name:
            config_changed = config_changed or model_name != self._model_name
            self._model_name = model_name
        if config_changed:
            # Never reuse a client created with stale credentials or endpoint data.
            self._client = None

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    @property
    def usage(self) -> dict[str, int]:
        """Return provider usage and independent text-request transport counters."""
        return dict(self._usage)

    def reset_usage(self) -> None:
        """Reset cumulative usage before a separately measured operation."""
        for key in self._usage:
            self._usage[key] = 0
        # Keep the legacy last-response usage shape; transport counters are
        # cumulative and may change even when there is no provider response.
        self.last_usage = {key: 0 for key in self.last_usage}

    async def _invoke_text_request(self, call, **kwargs):
        """Count SDK/HTTP invocation, not proof of server acceptance or billing."""
        self._usage["request_attempts"] += 1
        try:
            response = await call(**kwargs)
        except Exception as exc:
            if getattr(exc, "response", None) is not None:
                self._usage["responses_received"] += 1
            raise
        self._usage["responses_received"] += 1
        return response

    @staticmethod
    def _usage_value(usage: Any, *names: str) -> int:
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if isinstance(value, Number):
                return max(0, int(value))
        return 0

    def _record_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        usage_reported: bool = False,
    ) -> None:
        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = max(0, int(total_tokens or 0)) or prompt + completion
        self.last_usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "requests": 1,
        }
        for key, value in self.last_usage.items():
            self._usage[key] += value
        self._usage["usage_reports"] += int(usage_reported)

    async def chat(
        self,
        messages: List[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> str:
        """
        发送对话请求并返回响应文本

        支持的 provider:
        - openai / mimo / deepseek / zhipu / qwen / siliconflow / moonshot / custom → OpenAI 兼容协议
        - anthropic → Anthropic Messages API
        - ollama → Ollama 本地接口
        """
        # 所有国产模型 + openai + custom 都走 OpenAI 兼容协议
        openai_compatible = {
            "openai",
            "mimo",
            "deepseek",
            "zhipu",
            "qwen",
            "siliconflow",
            "moonshot",
            "sensenova",
            "custom",
        }
        if self.provider == "local":
            return await self._chat_local(messages, model, temperature, max_tokens)
        if self.provider in openai_compatible:
            return await self._chat_openai(messages, model, temperature, max_tokens, **kwargs)
        elif self.provider == "anthropic":
            return await self._chat_anthropic(messages, model, temperature, max_tokens, **kwargs)
        elif self.provider == "ollama":
            return await self._chat_ollama(messages, model, temperature, max_tokens, **kwargs)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def _chat_local(self, messages, model, temperature, max_tokens) -> str:
        """Call only the authenticated loopback service, never a proxy or cloud fallback."""
        import httpx
        from app.core.local_model import (
            LOCAL_MODEL_URL, LOCAL_MODEL_NAME, LOCAL_MAX_TOKENS,
            LOCAL_TIMEOUT_SECONDS, validate_local_model_url,
        )

        base_url = (self._base_url or LOCAL_MODEL_URL).rstrip("/")
        valid, error = validate_local_model_url(base_url)
        if not valid:
            raise ValueError(error)
        if not self._api_key:
            raise ValueError("本机模型服务凭证未配置，请先配置独立本机服务，不要填写云 API Key")
        if any(not isinstance(message.get("content"), str) for message in messages):
            raise ValueError("本机模型仅接受文字，不支持图片或其他多模态输入")
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=LOCAL_TIMEOUT_SECONDS,
        ) as client:
            response = await self._invoke_text_request(
                client.post, url=f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": model or self._model_name or LOCAL_MODEL_NAME,
                    "messages": messages, "temperature": temperature,
                    "max_tokens": min(max_tokens, LOCAL_MAX_TOKENS), "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()
        usage = data.get("usage") or {}
        self._record_usage(
            prompt_tokens=self._usage_value(usage, "prompt_tokens"),
            completion_tokens=self._usage_value(usage, "completion_tokens"),
            total_tokens=self._usage_value(usage, "total_tokens"),
            usage_reported=bool(usage),
        )
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("本机模型输出达到长度上限，请缩小问题范围后重试")
        content = choice["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise EmptyLLMResponseError("本机模型没有返回有效文字")
        return content

    async def test_connection(self) -> dict:
        """
        测试 LLM 连接

        Returns:
            包含 success(bool)、model(str)、可选 error(str) 的字典
        """
        try:
            request_options: dict[str, Any] = {}
            if self.provider == "zhipu":
                # GLM reasoning models can spend a tiny probe's whole token
                # budget on hidden reasoning and return an empty final answer.
                request_options["extra_body"] = {"thinking": {"type": "disabled"}}
            elif self.provider == "siliconflow":
                # Qwen3 enables reasoning by default on SiliconFlow. A tiny
                # connection probe should verify connectivity, not spend its
                # entire budget on hidden reasoning tokens.
                request_options["extra_body"] = {"enable_thinking": False}
            if self.provider in {
                "openai",
                "mimo",
                "deepseek",
                "zhipu",
                "qwen",
                "siliconflow",
                "moonshot",
                "sensenova",
                "custom",
            }:
                request_options["_max_retries"] = 0
            response = await self.chat(
                messages=[{"role": "user", "content": "Say hello in one word."}],
                # Reasoning models may spend the first tokens on hidden reasoning.
                # Keep this small, but leave enough room for visible content.
                max_tokens=64,
                **request_options,
            )
            return {
                "success": True,
                "model": self._get_default_model(),
                "response": response[:50],
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    async def _chat_openai(
        self,
        messages: List[dict],
        model: Optional[str],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> str:
        """Call an OpenAI-compatible provider with connection recovery."""
        import openai

        model_name = model or self._model_name or settings.OPENAI_DEFAULT_MODEL
        base_url = (self._base_url or settings.OPENAI_API_BASE or "").rstrip("/") or None
        endpoint_host = urlparse(base_url).netloc if base_url else "default"
        retry_override = kwargs.pop("_max_retries", None)
        max_retries = max(
            0,
            int(
                getattr(settings, "LLM_MAX_RETRIES", 3)
                if retry_override is None
                else retry_override
            ),
        )
        request_timeout = max(30.0, float(getattr(settings, "LLM_TIMEOUT", 60)))
        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                # 每次重试重建客户端，避免状态异常
                self._client = None
                content = await asyncio.wait_for(
                    self._chat_openai_once(
                        messages,
                        model_name,
                        temperature,
                        max_tokens,
                        **kwargs,
                    ),
                    timeout=request_timeout,
                )
                if not content or not content.strip():
                    raise EmptyLLMResponseError("LLM provider returned an empty response")
                return content
            except asyncio.CancelledError:
                # An outer task deadline cancels this coroutine before the
                # normal error path. Close the SDK client without retrying.
                await self._discard_openai_client()
                raise
            except Exception as exc:
                last_error = exc
                retryable = isinstance(
                    exc, (EmptyLLMResponseError, asyncio.TimeoutError)
                ) or self._is_retryable_openai_error(exc, openai)
                if not retryable or attempt >= max_retries:
                    break

                delay = min(8.0, float(2**attempt))
                logger.warning(
                    "Retrying LLM request after %s (provider=%s model=%s host=%s "
                    "attempt=%s/%s delay=%.1fs)",
                    type(exc).__name__,
                    self.provider,
                    model_name,
                    endpoint_host,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await self._discard_openai_client()
                await asyncio.sleep(delay)

        await self._discard_openai_client()
        error_name = type(last_error).__name__ if last_error else "UnknownError"
        raise RuntimeError(
            f"LLM request failed after {max_retries + 1} attempts "
            f"(provider={self.provider}, model={model_name}, host={endpoint_host}, "
            f"error={error_name}): {last_error}"
        ) from last_error

    @staticmethod
    def _is_retryable_openai_error(exc: Exception, openai_module) -> bool:
        """Return whether an OpenAI-compatible failure is safe to retry."""
        retryable_types = tuple(
            error_type
            for error_type in (
                getattr(openai_module, "APIConnectionError", None),
                getattr(openai_module, "APITimeoutError", None),
                getattr(openai_module, "RateLimitError", None),
                getattr(openai_module, "InternalServerError", None),
            )
            if isinstance(error_type, type)
        )
        if isinstance(exc, retryable_types):
            return True

        api_status_error = getattr(openai_module, "APIStatusError", None)
        if isinstance(api_status_error, type) and isinstance(exc, api_status_error):
            return getattr(exc, "status_code", None) in {408, 409, 425, 429, 500, 502, 503, 504}
        return False

    async def _discard_openai_client(self) -> None:
        """Close and forget a potentially broken pooled connection."""
        client = self._client
        self._client = None
        close = getattr(client, "close", None)
        if close:
            try:
                await close()
            except Exception:
                logger.debug("Failed to close LLM client", exc_info=True)

    async def _chat_openai_once(
        self,
        messages: List[dict],
        model: Optional[str],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> str:
        """调用 OpenAI Chat Completion API（含所有 OpenAI 兼容提供商）"""
        import openai

        api_key = (
            self._api_key if self._explicit_profile else self._api_key or settings.OPENAI_API_KEY
        )
        base_url = (
            self._base_url if self._explicit_profile else self._base_url or settings.OPENAI_API_BASE
        )
        if self._explicit_profile and not api_key:
            if self.provider == "custom":
                api_key = "not-required"
            else:
                raise ValueError(f"API key is not configured for provider: {self.provider}")

        if self._client is None:
            self._client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                # Retry is owned by _chat_openai so each failed attempt can
                # rebuild a potentially unhealthy pooled connection. Keeping
                # SDK retries enabled here would multiply attempts and latency.
                max_retries=0,
                timeout=120.0,
            )

        response = await self._invoke_text_request(
            self._client.chat.completions.create,
            model=model or self._model_name or settings.OPENAI_DEFAULT_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        usage = getattr(response, "usage", None)
        self._record_usage(
            prompt_tokens=self._usage_value(usage, "prompt_tokens", "input_tokens"),
            completion_tokens=self._usage_value(usage, "completion_tokens", "output_tokens"),
            total_tokens=self._usage_value(usage, "total_tokens"),
            usage_reported=usage is not None,
        )
        return response.choices[0].message.content

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    async def _chat_anthropic(
        self,
        messages: List[dict],
        model: Optional[str],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> str:
        """调用 Anthropic Messages API"""
        import anthropic

        retry_override = kwargs.pop("_max_retries", None)
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key or settings.ANTHROPIC_API_KEY,
            )
        # Internal retry controls belong to the SDK client, not the Messages
        # payload. Keep the override request-local when reusing the gateway.
        client = (
            self._client.with_options(max_retries=max(0, int(retry_override)))
            if retry_override is not None
            else self._client
        )

        # Anthropic 的 system 消息是独立参数，需要从 messages 中提取
        system_text = None
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                user_messages.append(msg)

        call_kwargs = dict(
            model=model or self._model_name or settings.ANTHROPIC_DEFAULT_MODEL,
            messages=user_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if system_text:
            call_kwargs["system"] = system_text
        call_kwargs.update(kwargs)

        try:
            response = await self._invoke_text_request(client.messages.create, **call_kwargs)
        except asyncio.CancelledError:
            await self._discard_openai_client()
            raise
        usage = getattr(response, "usage", None)
        self._record_usage(
            prompt_tokens=self._usage_value(usage, "input_tokens", "prompt_tokens"),
            completion_tokens=self._usage_value(usage, "output_tokens", "completion_tokens"),
            usage_reported=usage is not None,
        )
        return response.content[0].text

    # ------------------------------------------------------------------
    # Ollama
    # ------------------------------------------------------------------

    async def _chat_ollama(
        self,
        messages: List[dict],
        model: Optional[str],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> str:
        """调用 Ollama /api/chat 接口"""
        import httpx

        payload = {
            "model": model or self._model_name or settings.OLLAMA_DEFAULT_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with httpx.AsyncClient() as client:
            base_url = (self._base_url or settings.OLLAMA_BASE_URL).rstrip("/")
            response = await self._invoke_text_request(
                client.post,
                url=f"{base_url}/api/chat",
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            self._record_usage(
                prompt_tokens=self._usage_value(data, "prompt_eval_count"),
                completion_tokens=self._usage_value(data, "eval_count"),
                usage_reported="prompt_eval_count" in data or "eval_count" in data,
            )
            return data["message"]["content"]

    # ------------------------------------------------------------------
    # SenseNova 图像生成
    # ------------------------------------------------------------------

    async def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        image_size: str = "2k",
        negative_prompt: str = "",
        save_path: Optional[str] = None,
    ) -> dict:
        """
        调用 SenseNova-U1 图像生成 API

        Args:
            prompt: 图像描述
            aspect_ratio: 宽高比 (16:9, 1:1, 9:16 等)
            image_size: 图像尺寸 (1k, 2k)
            negative_prompt: 反向提示词
            save_path: 保存路径（可选）

        Returns:
            包含 status, output/url, message 的字典
        """
        import httpx

        api_key = self._api_key or settings.SENSENOVA_API_KEY
        base_url = (self._base_url or settings.SENSENOVA_API_BASE).rstrip("/")
        model = self._model_name or settings.SENSENOVA_DEFAULT_MODEL

        # 将 aspect_ratio 转为像素尺寸
        size = self._resolve_image_size(image_size.upper(), aspect_ratio)

        url = f"{base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "response_format": "url",
            "output_format": "png",
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            return {
                "status": "failed",
                "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
            }
        except httpx.HTTPError as e:
            return {"status": "failed", "error": f"Network error: {str(e)}"}
        except Exception as e:
            return {"status": "failed", "error": f"Request error: {str(e)}"}

        images_urls = [item.get("url") for item in data.get("data", []) if item.get("url")]
        if not images_urls:
            return {
                "status": "failed",
                "error": f"No image in response: {json.dumps(data, ensure_ascii=False)[:300]}",
            }

        image_url = images_urls[-1]

        # 可选：下载到本地
        if save_path:
            import os

            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            async with httpx.AsyncClient(timeout=120.0) as client:
                img_resp = await client.get(image_url)
                img_resp.raise_for_status()
                with open(save_path, "wb") as f:
                    f.write(img_resp.content)
            return {
                "status": "ok",
                "output": save_path,
                "url": image_url,
                "message": "Image generated successfully",
            }

        return {"status": "ok", "url": image_url, "message": "Image generated successfully"}

    @staticmethod
    def _resolve_image_size(resolution: str, aspect_ratio: str) -> str:
        """将分辨率+宽高比转为像素尺寸字符串（SenseNova-U1 有效尺寸）"""
        # SenseNova-U1 API 支持的完整尺寸列表
        buckets = {
            "2:3": (1664, 2496),
            "3:2": (2496, 1664),
            "3:4": (1760, 2368),
            "4:3": (2368, 1760),
            "4:5": (1824, 2272),
            "5:4": (2272, 1824),
            "1:1": (2048, 2048),
            "16:9": (2752, 1536),
            "9:16": (1536, 2752),
            "21:9": (3072, 1376),
            "9:21": (1344, 3136),
            "32:9": (2560, 720),
            "32:27": (3072, 864),
        }
        if aspect_ratio in buckets:
            w, h = buckets[aspect_ratio]
        else:
            # 默认 16:9
            w, h = buckets["16:9"]
        return f"{w}x{h}"

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _get_default_model(self) -> str:
        """获取当前 provider 的默认模型名"""
        if self._model_name:
            return self._model_name
        if self.provider == "openai":
            return settings.OPENAI_DEFAULT_MODEL
        elif self.provider == "anthropic":
            return settings.ANTHROPIC_DEFAULT_MODEL
        elif self.provider == "ollama":
            return settings.OLLAMA_DEFAULT_MODEL
        return "unknown"
