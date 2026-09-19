"""Conservative model capability hints used before task execution.

The registry intentionally reports ``unknown`` when an OpenAI-compatible custom
endpoint cannot be identified. It is a configuration guardrail, not a claim that
every model released by a provider has identical capabilities.
"""

from __future__ import annotations

from typing import Any

TASK_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "analysis": ("text",),
    "query_planning": ("structured_output",),
    "translation": ("text",),
    "vision": ("vision",),
    "recommendation": ("text",),
    "assistant": ("text",),
    "diagram": ("image_generation",),
    "embedding": ("embeddings",),
}

CAPABILITY_LABELS = {
    "text": {"zh": "文本", "en": "Text"},
    "structured_output": {"zh": "结构化输出", "en": "Structured output"},
    "vision": {"zh": "视觉理解", "en": "Vision"},
    "image_generation": {"zh": "图像生成", "en": "Image generation"},
    "embeddings": {"zh": "向量嵌入", "en": "Embeddings"},
}


def _contains(model: str, *needles: str) -> bool:
    return any(needle in model for needle in needles)


def infer_model_capabilities(
    provider: str,
    model_name: str,
) -> dict[str, bool | None]:
    """Infer known capabilities without sending a paid provider request."""
    provider = provider.casefold().strip()
    model = model_name.casefold().strip()
    capabilities: dict[str, bool | None] = {
        "text": True,
        "structured_output": True,
        "vision": False,
        "image_generation": False,
        "embeddings": False,
    }

    if provider == "local":
        capabilities["structured_output"] = None
        return capabilities

    if provider == "custom":
        capabilities.update(
            vision=None,
            image_generation=None,
            embeddings=None,
        )

    if provider == "openai":
        capabilities["vision"] = _contains(
            model, "gpt-4o", "gpt-4.1", "gpt-4-turbo", "o1", "o3", "o4"
        )
        capabilities["image_generation"] = _contains(model, "gpt-image", "dall-e")
    elif provider == "anthropic":
        capabilities["vision"] = _contains(model, "claude-3", "claude-4")
    elif provider == "mimo":
        capabilities["vision"] = _contains(model, "omni") or model == "mimo-v2.5"
    elif provider == "zhipu":
        capabilities["vision"] = _contains(
            model, "glm-4v", "glm-4.5v", "glm-4.6v", "vision", "-vl"
        )
    elif provider in {"qwen", "siliconflow"}:
        capabilities["vision"] = _contains(model, "-vl", "qwen-vl", "omni")
    elif provider == "ollama":
        capabilities["vision"] = _contains(
            model, "llava", "minicpm-v", "qwen2-vl", "qwen2.5-vl", "vision"
        )
    elif provider == "sensenova":
        capabilities["vision"] = _contains(model, "vision", "-vl", "omni")
        capabilities["image_generation"] = _contains(model, "u1", "image")

    if _contains(model, "embedding", "embed", "bge-", "nomic-", "e5-"):
        capabilities.update(
            text=False,
            structured_output=False,
            vision=False,
            image_generation=False,
            embeddings=True,
        )

    return capabilities


def assess_model_for_task(
    provider: str,
    model_name: str,
    task: str | None = None,
) -> dict[str, Any]:
    """Return a UI-safe compatibility report for one model/task pair."""
    capabilities = infer_model_capabilities(provider, model_name)
    requirements = TASK_REQUIREMENTS.get(task or "", ())
    values = [capabilities.get(item) for item in requirements]
    if not requirements:
        status = "unknown"
    elif provider == "local" and task != "assistant":
        status = "unsupported"
    elif any(value is False for value in values):
        status = "unsupported"
    elif any(value is None for value in values):
        status = "unknown"
    else:
        status = "supported"

    if status == "supported":
        reason_zh = "已知能力与此任务匹配。"
        reason_en = "Known capabilities match this task."
    elif status == "unsupported":
        reason_zh = "该模型的已知能力与此任务不匹配，请更换模型。"
        reason_en = "Known capabilities do not match this task; choose another model."
    else:
        reason_zh = "无法从模型名称确认能力；保存后请先测试再使用。"
        reason_en = "Capabilities cannot be confirmed from the model name; test before use."

    return {
        "provider": provider,
        "model_name": model_name,
        "task": task,
        "status": status,
        "requirements": list(requirements),
        "capabilities": capabilities,
        "labels": CAPABILITY_LABELS,
        "reason_zh": reason_zh,
        "reason_en": reason_en,
    }
