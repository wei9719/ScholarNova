"""
模型配置相关 API 端点
"""

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from app.core.rate_limiter import check_rate_limit
from app.core.ssrf import validate_base_url
from app.core.local_model import LOCAL_MODEL_URL, validate_model_endpoint
from app.schemas.search import (
    EmbeddingModelConfig,
    LLMProviderName,
    ModelCapabilityProbeRequest,
    ModelCapabilityProbeResponse,
    ModelConfig,
    ModelTestRequest,
    ModelTestResponse,
    SuccessResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/capabilities")
async def get_model_capabilities(
    provider: LLMProviderName,
    model_name: str,
    task: str | None = None,
) -> dict:
    """Inspect model/task compatibility without sending a provider request."""
    from app.services.inference.capabilities import assess_model_for_task

    return assess_model_for_task(provider, model_name, task)


def _read_saved_config() -> dict:
    """Read the local runtime config without exposing it to callers."""
    import json

    from app.config import runtime_path

    config_path = runtime_path("model_config.json")
    try:
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
                return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
    return {}


def _resolve_probe_profile(request: ModelCapabilityProbeRequest) -> dict:
    """Resolve one isolated probe profile without leaking credentials across providers."""
    from app.config import PROVIDER_DEFAULTS, settings

    saved = _read_saved_config()
    saved_provider = str(saved.get("provider") or settings.DEFAULT_LLM_PROVIDER)
    saved_tasks = saved.get("tasks") if isinstance(saved.get("tasks"), dict) else {}
    task_config = saved_tasks.get(request.task)
    if not isinstance(task_config, dict):
        task_config = {}

    if request.provider == "local":
        base_url = request.base_url or task_config.get("base_url") or LOCAL_MODEL_URL
        matching_local_task = (
            task_config.get("provider") == "local"
            and (task_config.get("base_url") or LOCAL_MODEL_URL).rstrip("/") == base_url.rstrip("/")
            and task_config.get("model_name") == request.model_name
        )
        return {
            "provider": "local", "model": request.model_name, "base_url": base_url,
            "api_key": request.api_key or (task_config.get("api_key") if matching_local_task else None),
        }

    matching_task = str(task_config.get("provider") or saved_provider) == request.provider
    api_key = request.api_key
    base_url = request.base_url
    if matching_task:
        api_key = api_key or task_config.get("api_key")
        base_url = base_url or task_config.get("base_url")

    if request.provider == saved_provider:
        api_key = api_key or saved.get("api_key") or settings.OPENAI_API_KEY
        base_url = base_url or saved.get("base_url") or settings.OPENAI_API_BASE

    saved_fallback = saved.get("fallback")
    if not isinstance(saved_fallback, dict):
        saved_fallback = {}
    if request.provider == saved_fallback.get("provider"):
        api_key = api_key or saved_fallback.get("api_key")
        base_url = base_url or saved_fallback.get("base_url")

    if request.provider == "sensenova":
        api_key = api_key or settings.SENSENOVA_API_KEY
        base_url = base_url or settings.SENSENOVA_API_BASE

    provider_base_url = PROVIDER_DEFAULTS.get(request.provider, (None, None))[0]
    return {
        "provider": request.provider,
        "model": request.model_name,
        "api_key": api_key,
        "base_url": base_url or provider_base_url,
    }


def _redact_probe_error(error: str | None, api_key: str | None) -> str | None:
    if not error:
        return None
    safe = str(error)
    if api_key:
        safe = safe.replace(api_key, "[REDACTED]")
    return safe[:800]


@router.get(
    "/capabilities/probe",
    response_model=ModelCapabilityProbeResponse | None,
)
async def get_saved_capability_probe(
    provider: LLMProviderName,
    model_name: str,
    task: str,
) -> ModelCapabilityProbeResponse | None:
    """Return the most recent local result; this never calls a provider."""
    from app.services.inference.capability_probe import latest_probe_report

    return latest_probe_report(provider, model_name, task)


@router.post(
    "/capabilities/probe",
    response_model=ModelCapabilityProbeResponse,
)
async def probe_model_capability(
    request: ModelCapabilityProbeRequest,
    http_request: Request,
) -> ModelCapabilityProbeResponse:
    """Run one user-triggered, task-specific real model request."""
    limited = check_rate_limit(http_request, endpoint_type="analysis")
    if limited:
        return limited

    profile = _resolve_probe_profile(request)
    if profile.get("base_url"):
        is_valid, error = validate_model_endpoint(profile["provider"], profile["base_url"])
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"API 地址不安全: {error}")

    from app.services.inference.capability_probe import (
        run_capability_probe,
        save_probe_report,
    )

    report = await run_capability_probe(profile, request.task)
    report["error"] = _redact_probe_error(
        report.get("error"),
        str(profile.get("api_key") or "") or None,
    )
    try:
        save_probe_report(report)
    except OSError as exc:
        logger.warning("Unable to save local capability probe report: %s", exc)
    return ModelCapabilityProbeResponse(**report)


@router.get("/config")
async def get_model_config() -> dict:
    """Return the active local model config with every credential removed."""
    from app.config import settings

    saved = _read_saved_config()
    provider = saved.get("provider") or settings.DEFAULT_LLM_PROVIDER
    safe_tasks: dict[str, dict] = {}
    saved_tasks = saved.get("tasks")
    if not isinstance(saved_tasks, dict):
        saved_tasks = {}
    for task_name, task_config in saved_tasks.items():
        if not isinstance(task_config, dict):
            continue
        safe_task = dict(task_config)
        safe_task["api_key_configured"] = bool(safe_task.get("api_key"))
        safe_task["api_key"] = None
        safe_tasks[task_name] = safe_task

    saved_fallback = saved.get("fallback")
    if not isinstance(saved_fallback, dict):
        saved_fallback = {}
    fallback_provider = saved_fallback.get("provider") or "qwen"
    fallback_default_urls = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "ollama": "http://localhost:11434",
        "mimo": "https://token-plan-cn.xiaomimimo.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "moonshot": "https://api.moonshot.cn/v1",
        "sensenova": "https://token.sensenova.cn/v1",
    }
    safe_fallback = {
        "enabled": bool(saved_fallback.get("enabled")),
        "provider": fallback_provider,
        "model_name": saved_fallback.get("model_name") or "qwen-plus",
        "api_key": None,
        "api_key_configured": bool(saved_fallback.get("api_key")),
        "base_url": saved_fallback.get("base_url") or fallback_default_urls.get(fallback_provider),
    }

    saved_embedding = saved.get("embedding")
    if not isinstance(saved_embedding, dict):
        saved_embedding = {}
    embedding_provider = saved_embedding.get("provider") or "ollama"
    embedding_default_urls = {
        "ollama": "http://localhost:11434",
        "openai": "https://api.openai.com/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
    }
    safe_embedding = {
        "enabled": bool(saved_embedding.get("enabled")),
        "provider": embedding_provider,
        "model_name": saved_embedding.get("model_name") or "nomic-embed-text",
        "api_key": None,
        "api_key_configured": bool(saved_embedding.get("api_key")),
        "base_url": saved_embedding.get("base_url")
        or embedding_default_urls.get(embedding_provider),
    }

    return {
        "provider": provider,
        "model_name": saved.get("model_name") or settings.OPENAI_DEFAULT_MODEL,
        "api_key": None,
        "api_key_configured": bool(saved.get("api_key") or settings.OPENAI_API_KEY),
        "base_url": saved.get("base_url") or settings.OPENAI_API_BASE,
        "temperature": saved.get("temperature", 0.7),
        "max_tokens": saved.get("max_tokens", 4096),
        "tasks": safe_tasks,
        "fallback": safe_fallback,
        "embedding": safe_embedding,
    }


@router.post("/config", response_model=SuccessResponse)
async def save_model_config(
    config: ModelConfig,
) -> SuccessResponse:
    """
    保存模型配置

    保存用户的 LLM 模型配置到内存缓存和本地文件
    """
    import json

    # 更新全局多模型配置
    from app.config import MODEL_PROFILES, runtime_path, settings

    existing_config = _read_saved_config()
    # The optional small local model is deliberately confined to one text task.
    if config.provider == "local":
        raise HTTPException(status_code=400, detail="本机模型目前仅用于智能体文字任务，请保留默认云模型")
    if ((config.fallback and config.fallback.provider == "local")
            or (config.embedding and config.embedding.provider == "local")):
        raise HTTPException(status_code=400, detail="本机文字模型不能作为全局备用或向量模型")
    for task_name, task_config in (config.tasks or {}).items():
        if task_config.provider == "local":
            if task_name != "assistant":
                raise HTTPException(status_code=400, detail="本机模型目前仅支持智能体文字任务")
            valid, error = validate_model_endpoint("local", task_config.base_url)
            if not valid:
                raise HTTPException(status_code=400, detail=error)
    existing_provider = existing_config.get("provider")
    existing_tasks = existing_config.get("tasks")
    if not isinstance(existing_tasks, dict):
        existing_tasks = {}
    base_url = config.base_url
    model_name = config.model_name
    provider = config.provider
    api_key = config.api_key or (
        existing_config.get("api_key") if existing_provider == provider else None
    )
    config_data = config.model_dump()
    config_data["api_key"] = api_key

    existing_fallback = existing_config.get("fallback")
    if not isinstance(existing_fallback, dict):
        existing_fallback = {}
    if config.fallback is None:
        if existing_fallback:
            config_data["fallback"] = existing_fallback
        else:
            config_data.pop("fallback", None)
    else:
        fallback_data = config.fallback.model_dump()
        fallback_provider = fallback_data.get("provider") or "qwen"
        previous_fallback_provider = existing_fallback.get("provider")
        fallback_data["api_key"] = fallback_data.get("api_key") or (
            existing_fallback.get("api_key")
            if previous_fallback_provider == fallback_provider
            else None
        )
        if fallback_data.get("enabled") and not fallback_data.get("model_name"):
            raise HTTPException(status_code=400, detail="备用模型名称不能为空")
        if fallback_data.get("enabled") and fallback_data.get("base_url"):
            is_valid, error = validate_base_url(fallback_data["base_url"])
            if not is_valid:
                raise HTTPException(
                    status_code=400,
                    detail=f"备用模型 API 地址不安全: {error}",
                )
        config_data["fallback"] = fallback_data

    existing_embedding = existing_config.get("embedding")
    if not isinstance(existing_embedding, dict):
        existing_embedding = {}
    if config.embedding is None:
        if existing_embedding:
            config_data["embedding"] = existing_embedding
        else:
            config_data.pop("embedding", None)
    else:
        embedding_data = config.embedding.model_dump()
        embedding_provider = embedding_data.get("provider") or "ollama"
        previous_embedding_provider = existing_embedding.get("provider")
        embedding_data["api_key"] = embedding_data.get("api_key") or (
            existing_embedding.get("api_key")
            if previous_embedding_provider == embedding_provider
            else None
        )
        if embedding_data.get("enabled") and embedding_data.get("base_url"):
            is_valid, error = validate_base_url(embedding_data["base_url"])
            if not is_valid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Embedding API 地址不安全: {error}",
                )
        config_data["embedding"] = embedding_data

    if config.tasks:
        # 有任务级配置，更新对应的任务
        for task_name, task_config in config.tasks.items():
            if task_name in MODEL_PROFILES:
                task_provider = task_config.provider or provider
                same_provider = task_provider == provider
                previous_task = existing_tasks.get(task_name) or {}
                previous_provider = previous_task.get("provider") or existing_provider
                task_api_key = task_config.api_key or (
                    previous_task.get("api_key")
                    if previous_provider == task_provider
                    else (api_key if same_provider else None)
                )
                MODEL_PROFILES[task_name] = {
                    "provider": task_provider,
                    "model": task_config.model_name or (model_name if same_provider else None),
                    "api_key": task_api_key,
                    "base_url": task_config.base_url or (base_url if same_provider else None),
                }
                config_data["tasks"][task_name]["api_key"] = task_api_key

    # 所有未单独配置的任务用主配置
    for task_name in MODEL_PROFILES:
        if config.tasks and task_name in config.tasks:
            continue
        MODEL_PROFILES[task_name] = {
            "provider": provider,
            "model": model_name,
            "api_key": api_key,
            "base_url": base_url,
        }

    # 更新全局设置
    if api_key:
        settings.OPENAI_API_KEY = api_key
    settings.DEFAULT_LLM_PROVIDER = provider
    if base_url:
        settings.OPENAI_API_BASE = base_url
    if model_name:
        settings.OPENAI_DEFAULT_MODEL = model_name

    # 保存到本地文件
    config_path = runtime_path("model_config.json")
    try:
        with config_path.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"保存配置文件失败: {e}")

    return SuccessResponse(
        success=True,
        message="模型配置已保存",
    )


@router.post("/test-embedding", response_model=ModelTestResponse)
async def test_embedding_connection(
    request: EmbeddingModelConfig,
    http_request: Request,
) -> ModelTestResponse:
    """Test an embedding profile without saving it or calling the chat model."""
    limited = check_rate_limit(http_request, endpoint_type="analysis")
    if limited:
        return limited
    if request.base_url:
        is_valid, error = validate_model_endpoint(request.provider, request.base_url)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=f"Embedding API 地址不安全: {error}",
            )

    from app.services.retrieval.embeddings import EmbeddingGateway

    start_time = time.time()
    try:
        gateway = EmbeddingGateway(
            {
                "provider": request.provider,
                "model": request.model_name,
                "api_key": request.api_key,
                "base_url": request.base_url,
            }
        )
        result = await asyncio.wait_for(
            gateway.embed(["ScholarNova semantic retrieval connection test"]),
            timeout=15.0,
        )
        return ModelTestResponse(
            success=True,
            latency_ms=(time.time() - start_time) * 1000,
            model_info={
                "provider": request.provider,
                "model": request.model_name,
                "dimensions": len(result.vectors[0]),
                "input_tokens": result.input_tokens,
            },
        )
    except TimeoutError:
        return ModelTestResponse(
            success=False,
            latency_ms=(time.time() - start_time) * 1000,
            error="Embedding 连接测试超过 15 秒。",
        )
    except Exception as exc:
        return ModelTestResponse(
            success=False,
            latency_ms=(time.time() - start_time) * 1000,
            error=str(exc),
        )


@router.post("/test", response_model=ModelTestResponse)
async def test_model_connection(
    request: ModelTestRequest,
    http_request: Request,
) -> ModelTestResponse:
    """
    测试模型连通性

    测试 LLM 模型配置是否正确，能否正常调用
    """
    # 速率限制检查
    rate_limit_response = check_rate_limit(http_request, endpoint_type="analysis")
    if rate_limit_response:
        return rate_limit_response

    # SSRF 防护：验证 base_url
    if request.base_url:
        is_valid, error = validate_model_endpoint(request.provider, request.base_url)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=f"API 地址不安全: {error}",
            )

    from app.services.llm.gateway import LLMGateway

    start_time = time.time()

    try:
        # 连接测试也使用隔离配置，避免把当前主供应商的 Key
        # 误发给正在测试的另一个供应商。
        from app.config import settings

        saved_fallback = _read_saved_config().get("fallback")
        if not isinstance(saved_fallback, dict):
            saved_fallback = {}
        api_key = request.api_key
        if request.provider == "local" and not api_key:
            task = (_read_saved_config().get("tasks") or {}).get("assistant") or {}
            if (task.get("provider") == "local"
                    and (task.get("base_url") or LOCAL_MODEL_URL).rstrip("/")
                    == (request.base_url or LOCAL_MODEL_URL).rstrip("/")
                    and task.get("model_name") == request.model_name):
                api_key = task.get("api_key")
        if not api_key and request.provider != "local" and request.provider == settings.DEFAULT_LLM_PROVIDER:
            api_key = settings.OPENAI_API_KEY
        elif not api_key and request.provider != "local" and saved_fallback.get("provider") == request.provider:
            api_key = saved_fallback.get("api_key")
        # 测试连接优先使用该供应商自己的默认地址兜底，避免 base_url 为空时
        # 误用 .env 或其他已保存配置的地址，导致 Key 打到错误的厂商。
        from app.config import PROVIDER_DEFAULTS as _PD

        _default_base, _ = _PD.get(request.provider, (None, None))
        _target_base = request.base_url or _default_base or settings.OPENAI_API_BASE
        gateway = LLMGateway.from_profile(
            {
                "provider": request.provider,
                "api_key": api_key,
                "base_url": _target_base,
                "model": request.model_name,
            }
        )

        # 发送测试请求
        # A connection check must return promptly even when a provider, proxy,
        # or key is invalid. Full analysis requests keep their own bounded retry
        # policy; the settings-page probe has a strict user-facing deadline.
        result = await asyncio.wait_for(gateway.test_connection(), timeout=15.0)
        latency_ms = (time.time() - start_time) * 1000

        if result["success"]:
            return ModelTestResponse(
                success=True,
                latency_ms=latency_ms,
                model_info={
                    "provider": request.provider,
                    "model": request.model_name,
                },
                error=None,
            )
        else:
            return ModelTestResponse(
                success=False,
                latency_ms=latency_ms,
                model_info=None,
                error=result.get("error", "Unknown error"),
            )

    except TimeoutError:
        latency_ms = (time.time() - start_time) * 1000
        return ModelTestResponse(
            success=False,
            latency_ms=latency_ms,
            model_info=None,
            error="连接测试超过 15 秒，请稍后重试或选择负载较低的模型。",
        )
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        return ModelTestResponse(
            success=False,
            latency_ms=latency_ms,
            model_info=None,
            error=str(e),
        )
